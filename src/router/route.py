"""Layer 4 — the router: from schema fields to a FieldPlan.

The field-driven inversion. Instead of surveying every source and hoping values
fall out, the router starts from *what it must fill* — the target schema's leaf
fields (:func:`~src.router.schema.walk_schema`) — and routes each to the source
that can answer it. A field falls into one of three buckets:

- **tool** — a property of the data itself (record count), computed by a
  deterministic tool, no search;
- **column** — "which column?", routed over the *enriched* catalog
  (:meth:`Catalog.search`), so opaque names resolved in layer 3 are reachable;
- **document** — a meaning stated only in prose (abstract, licence), routed over
  the document sources.

Each bucket names **where the answer comes from**, so a routing can be read
without recalling a definition. A field nothing answers is ``unanswered``.

**Ranking alone over-answers, so two adjudicators sit above it.** BM25's only
reject rule is a non-empty score, which on a labeled bundle answered 88% of a
schema whose true answerable rate was 24% — schema and data are written by
different people, so lexical overlap is coincidence in both directions. Above the
ranking, therefore:

- :mod:`src.router.type_fit` (4a, deterministic) grades whether a candidate's type
  and units fit the field — a name field against an integer column, a whole-number
  field against fractional values. A mismatch caps the routing's assurance at
  ``low``; it never removes a candidate, so a blunt rule cannot hide a right answer.
- the judges (4b, optional, a model) decide what answers each field, or that nothing
  does:
  a :mod:`column matcher <src.router.column_matcher>` for the structured tier and a
  :mod:`passage reader <src.router.passage_reader>` for the documents. Their most
  valuable answer is *none*.

With a judge, ``candidates[0]`` is the judge's pick rather than BM25's, so the
bucket and (downstream) the task's resources follow a judgement instead of corpus
iteration order. The ordering is the whole seam; no consumer changes.

Routing runs as **two passes, not field by field**: the structured tier for every
field, then the document tier for whatever the first could not answer. With judges,
the first pass matches every field against the whole catalog, and the second reads
each retrieved passage once for all the fields that retrieved it. A judge is a
network call and a schema is dozens of fields, so these shapes — one catalog, many
fields; one passage, many fields — are what keep a routing pass affordable.

**BM25 still runs with judges on**, for two reasons: without a judge its rank 1 *is*
the routing, and with one its ranking is the record of what retrieval proposed —
what the evaluation's recall@k measures, and what a routing's rejected candidates
show.

The output is a :class:`FieldPlan` — the persisted routing artifact and the
source of truth the M4 compiler turns into executable `Task`s. Coverage falls
straight out of it: a field the router cannot route is flagged **before**
extraction, which is the confabulation signal moved upstream.

Scope (M3): the router builds the artifact by calling the search *methods*
directly. When it later drives execution (M4), those searches fire through the
`search_context` tool under ``attributed_to(Caller(phase="route"))`` so each
becomes provenance-captured evidence; that wiring is deliberately not here yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel

from src.context.base_context import (
    EvidenceRef,
    Searchable,
    bm25_scores,
    content_terms,
    tokenize,
)
from src.router.catalog import Catalog, locate_quote
from src.router.column_matcher import (
    ColumnGroup,
    ColumnMatcher,
    column_ref,
    group_card,
    merge_columns,
    tool_card,
)
from src.router.judge import TOOL_PREFIX, Verdict, candidate_ref, rank_of, weaker
from src.router.passage_reader import PassageReader, passage_card
from src.router.type_fit import mismatch, mismatches
from src.router.schema import FieldSpec, walk_schema


@dataclass
class FieldRouting:
    """Where one field is routed, and on what candidate evidence."""

    field_path: str
    query: str                              # the field description, used as the query
    bucket: str                             # tool | column | document | unanswered
    candidates: List[EvidenceRef] = field(default_factory=list)
    assurance: str = "none"                 # high | medium | low | none
    status: str = "routed"                  # routed | unanswered
    # Populated when a judge decided the field (layer 4b):
    judge_choice: Optional[str] = None     # the ref it picked, None when it abstained
    judge_note: Optional[str] = None       # why it picked that one, or why none
    judge_quote: Optional[str] = None      # the sentence it cited from that candidate
    judge_grounded: Optional[bool] = None  # could its quote be found in the material?
    citation: Optional[str] = None         # resource#start-end of the cited sentence
    mismatches: List[str] = field(default_factory=list)  # candidates whose type/units do not fit
    # Populated by the M4 compiler, not the router:
    extractor_role: Optional[str] = None
    topology: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_path": self.field_path,
            "query": self.query,
            "bucket": self.bucket,
            "status": self.status,
            "assurance": self.assurance,
            "judge_choice": self.judge_choice,
            "judge_note": self.judge_note,
            "judge_quote": self.judge_quote,
            "judge_grounded": self.judge_grounded,
            "citation": self.citation,
            "mismatches": self.mismatches,
            "candidates": [c.to_dict() for c in self.candidates],
            "extractor_role": self.extractor_role,
            "topology": self.topology,
        }


@dataclass
class FieldPlan:
    """The persisted routing artifact: one FieldRouting per leaf field."""

    schema_name: str
    routings: Dict[str, FieldRouting]

    def unanswered(self) -> List[str]:
        """Fields with no candidate source — flagged before extraction runs."""
        return [p for p, r in self.routings.items() if r.status == "unanswered"]

    def coverage(self) -> Dict[str, Any]:
        """A one-glance report: how many fields routed, where, and how grounded."""
        by_bucket: Dict[str, int] = {}
        by_assurance: Dict[str, int] = {}
        for r in self.routings.values():
            by_bucket[r.bucket] = by_bucket.get(r.bucket, 0) + 1
            by_assurance[r.assurance] = by_assurance.get(r.assurance, 0) + 1
        total = len(self.routings)
        unanswered = self.unanswered()
        return {
            "total": total,
            "routed": total - len(unanswered),
            "unanswered": unanswered,
            "by_bucket": by_bucket,
            "by_assurance": by_assurance,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "routings": {p: r.to_dict() for p, r in self.routings.items()},
            "coverage": self.coverage(),
        }


#: How wide a document passage may be. The same budget layer 3 packs prose reads
#: into, and the same size as a document it would otherwise take in one go.
_PASSAGE_MAX_CHARS = 20_000


def _pack(chunks: List[Any]) -> List[List[Any]]:
    """Group *consecutive* chunks into runs that fit the passage budget.

    Consecutive, unlike layer 3's packing of scattered retrieval hits, because a
    router candidate's locator is a span: keeping a passage contiguous is what makes
    ``text[start:end]`` the passage exactly, so a citation located inside it stays a
    true document offset. A chunk over the budget is a passage by itself — a chunk is
    never split, since it is what offsets point into.
    """
    runs: List[List[Any]] = []
    current: List[Any] = []
    size = 0
    for chunk in chunks:
        if current and size + len(chunk.text) > _PASSAGE_MAX_CHARS:
            runs.append(current)
            current, size = [], 0
        current.append(chunk)
        size += len(chunk.text)
    if current:
        runs.append(current)
    return runs


def _document_corpus(
    docs: List[Searchable], packed: bool = True
) -> Tuple[List[EvidenceRef], List[List[str]]]:
    """Every document as candidates — packed into passages, or chunk by chunk.

    **The router only widens what it offers when something can narrow it again.**
    ``packed`` is therefore the passage reader's presence. With a judge, whole passages go out
    and come back narrowed to the cited sentence (:func:`_cite`) — broad retrieval,
    precise result. Without one, rank 1 *is* the answer, so a passage-wide candidate
    would hand the compiler a whole section where it used to get the right paragraph,
    and every field of one small document would seed the identical span. Unjudged
    retrieval has to be precise, because nothing downstream will fix it.

    Ranking the chunks and keeping the best three answers the wrong question. A field
    description is written in schema vocabulary and the document in the author's, so
    the paragraph that answers a field routinely shares no word with it: ``photoperiod``
    against "a 12:12-h light–dark cycle" scores zero, and a paragraph naming the
    species, the temperature, the food and the life stage is dropped for every one of
    those fields at once. That is not a ranking a better lexical scorer fixes — the
    term is absent, and a filter cannot recover what it discarded.

    So nothing is discarded. The corpus is packed into whole passages and *all* of them
    are offered, ordered by score; with a small document set every passage survives the
    top-``k`` cut, and a large one still degrades gently, since ``k`` passages cover an
    order of magnitude more text than ``k`` chunks did.

    Built once per routing pass: chunking and tokenizing are per corpus, not per field.
    """
    refs: List[EvidenceRef] = []
    tokens: List[List[str]] = []
    for doc in docs:
        for resource in getattr(doc, "resources", []):
            chunks = list(doc.iter_chunks(resource))
            if not chunks:
                continue
            text = doc.read_text(resource)
            for run in (_pack(chunks) if packed else [[c] for c in chunks]):
                start = run[0].start_offset
                end = run[-1].start_offset + len(run[-1].text)
                passage = text[start:end]
                refs.append(
                    EvidenceRef(
                        resource=resource, locator=(start, end), kind="quoted_span",
                        snippet=passage[:200] + ("…" if len(passage) > 200 else ""),
                        score=0.0,
                    )
                )
                tokens.append(tokenize(passage))
    return refs, tokens


def _search_docs(
    corpus: Tuple[List[EvidenceRef], List[List[str]]],
    query: str,
    k: int,
    offer_unscored: bool = False,
) -> List[EvidenceRef]:
    """Order the document passages against one field's query, best first.

    ``offer_unscored`` decides what happens when *nothing* scores — the ``photoperiod``
    case, where the field's every term is absent from the corpus and the answer is in
    it anyway. Offering the passages regardless is the only way such a field can be
    answered, **and it is only defensible when something can refuse them**: a judge
    may answer "none", where a bare ranking takes rank 1 on faith. So the router
    passes it only with a judge. Without one an empty result stands, and the field is
    reported ``unanswered`` — a coverage gap is the honest reading of a corpus the
    query cannot reach, and far better than a coin flip with a citation attached.
    """
    refs, tokens = corpus
    if not refs:
        return []
    scores = bm25_scores(content_terms(query), tokens)
    ranked = [
        EvidenceRef(
            resource=r.resource, locator=r.locator, kind=r.kind,
            snippet=r.snippet, score=s,
        )
        for r, s in zip(refs, scores)
        if s > 0
    ]
    if not ranked and offer_unscored:
        ranked = list(refs)
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:k]


def _passage_reader(docs: List[Searchable]):
    """Resolve a span candidate back to the **full text it points at**.

    ``EvidenceRef.snippet`` is a 200-character preview — its stated purpose — while a
    retrieved chunk runs to thousands. Handing the judge the preview withholds most of
    what retrieval just found, and the sentence that answers the field is as likely to
    fall past the cut as before it. The span is the pointer; this dereferences it.

    Returns ``None`` for anything that is not a located span, which is every structured
    candidate: a column's card is what layer 3 resolved, not a slice of a document.
    """
    by_resource: Dict[str, Searchable] = {}
    for doc in docs:
        for resource in getattr(doc, "resources", []):
            by_resource.setdefault(resource, doc)

    def read(candidate: EvidenceRef) -> Optional[str]:
        locator = candidate.locator
        doc = by_resource.get(candidate.resource)
        if candidate.kind != "quoted_span" or doc is None:
            return None
        if not isinstance(locator, (tuple, list)) or len(locator) != 2:
            return None
        start, end = locator
        return doc.read_text(candidate.resource)[start:end]

    return read


def _cite(
    candidate: EvidenceRef, quote: str, passage
) -> Tuple[EvidenceRef, Optional[str]]:
    """Narrow a span candidate to the sentence the judge actually cited.

    The chunk is where *retrieval* stopped; the quote is where the answer is. Locating
    one inside the other is the same proposes/disposes move layer 3 makes for a prose
    read (:func:`~src.router.catalog.locate_quote`): the model proposes a verbatim
    sentence, and finding it in the source disposes of it — yielding a citation a
    verifier can check by reading, instead of a 2 000-character chunk whose boundaries
    are an artifact of the chunker.

    A paraphrase that cannot be located leaves the candidate at chunk width. The
    routing still carries ``judge_grounded=False``, so an unlocatable quote is already
    graded; this only declines to invent a span for it.
    """
    text = passage(candidate) if passage else None
    span = locate_quote(quote, text) if text else None
    if span is None or not isinstance(candidate.locator, (tuple, list)):
        return candidate, None
    base = candidate.locator[0]
    start, end = base + span[0], base + span[1]
    located = EvidenceRef(
        resource=candidate.resource, locator=(start, end), kind=candidate.kind,
        # The snippet becomes the cited sentence itself: a preview of a chunk is a
        # worse handoff than the one sentence that was judged to answer the field.
        snippet=text[span[0] : span[1]], score=candidate.score,
    )
    return located, f"{candidate.resource}#{start}-{end}"


def _answer_tools():
    """The registered field-answering tools (importing registers them)."""
    import src.tools  # noqa: F401 — ensure the tool registry is populated
    from src.tools.base import field_answering_tools

    return field_answering_tools()


def _structured_candidates(
    query: str, catalog: Optional[Catalog], k: int
) -> List[EvidenceRef]:
    """Rank the query against the *structured* corpus, standard-agnostically.

    The corpus is the field-answering tools (by their own descriptions) plus the
    enriched columns, pooled into one BM25 ranking — both are short, comparable
    documents, so a tool and a column compete on equal footing. The winning
    candidate's ``kind`` then names the bucket: a tool → ``structural`` (bound by
    the tool's declared purpose, not a per-standard keyword), a column →
    ``ambiguous_structural``. No field-name table anywhere.
    """
    entries: List[EvidenceRef] = []
    docs: List[List[str]] = []

    for tool in _answer_tools():
        entries.append(
            EvidenceRef(
                resource="", locator=tool.name, kind="tool",
                snippet=tool.description or tool.name, score=0.0,
            )
        )
        docs.append(tokenize(f"{tool.name} {tool.description or ''}"))

    if catalog is not None:
        for col in catalog.columns:
            entries.append(
                EvidenceRef(
                    resource=col.resource, locator=col.name, kind="computed_column",
                    snippet=f"{col.name}: {col.description or col.value_label or col.dtype}",
                    score=0.0,
                )
            )
            docs.append(tokenize(col.document()))

    scores = bm25_scores(content_terms(query), docs)
    ranked = [
        EvidenceRef(
            resource=e.resource, locator=e.locator, kind=e.kind,
            snippet=e.snippet, score=s,
        )
        for e, s in zip(entries, scores)
        if s > 0
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:k]


def _routing(
    spec: FieldSpec,
    candidates: List[EvidenceRef],
    catalog: Optional[Catalog],
    decision: Optional[Verdict] = None,
    citation: Optional[str] = None,
) -> FieldRouting:
    """A routed field, read off ``candidates[0]``; ``decision`` when a judge made it.

    Two hops when judged: the judge's confidence in the *match*, and the catalog's in
    the column's *meaning*. The routing is only as strong as the weaker. A winner whose
    type or units do not fit the field (layer 4a) caps it at ``low`` on top: it may
    still be right, so it stays, but it is a routing to check rather than trust.
    """
    top = candidates[0]
    bucket = _bucket_of(top)
    assurance = _assurance(bucket, top, catalog)
    if decision is not None:
        assurance = weaker(decision.confidence, assurance)
    if _misfits(spec, top, catalog):
        assurance = weaker(assurance, "low")
    return FieldRouting(
        field_path=spec.path, query=spec.description or spec.path, bucket=bucket,
        candidates=candidates, assurance=assurance,
        judge_choice=decision.choice if decision else None,
        judge_note=(decision.because or None) if decision else None,
        # The quote is the sentence the reader cited, verified present in the passage.
        # For a document field it is the nearest thing to the answer the router holds,
        # so it travels on the routing rather than being re-derived downstream.
        judge_quote=(decision.quote or None) if decision else None,
        judge_grounded=decision.grounded if decision else None,
        citation=citation,
        mismatches=mismatches(spec, candidates, catalog),
    )


def _misfits(spec: FieldSpec, top: EvidenceRef, catalog: Optional[Catalog]) -> bool:
    """Does the winning column's type or units not fit the field?"""
    if top.kind != "computed_column" or catalog is None:
        return False
    column = catalog.find(top.locator, top.resource)
    return column is not None and mismatch(spec, column) is not None


def _unanswered(
    spec: FieldSpec,
    rejected: List[EvidenceRef],
    judged: bool,
    catalog: Optional[Catalog],
) -> FieldRouting:
    """A field no tier could answer — coverage, computed before extraction runs."""
    return FieldRouting(
        field_path=spec.path, query=spec.description or spec.path,
        bucket="unanswered",
        # With a judge the rejected set is the record of what was considered and
        # refused; without one an empty list keeps the historical shape.
        candidates=rejected if judged else [],
        assurance="none", status="unanswered",
        judge_note="judge found no candidate that answers this field"
        if judged and rejected
        else None,
        mismatches=mismatches(spec, rejected, catalog),
    )


# ---------------------------------------------------------------------------
# Tier 1 with a judge: match every field against the whole catalog
# ---------------------------------------------------------------------------


@dataclass
class _Matching:
    """What the column matcher decided, and what it needs to fan a match back out."""

    verdicts: Dict[str, Verdict]
    groups: Dict[str, ColumnGroup]            # group ref -> its columns
    tools: Dict[str, str]                     # tool name -> description


def _match(
    matcher: ColumnMatcher,
    specs: List[FieldSpec],
    catalog: Optional[Catalog],
) -> _Matching:
    """Match every field against the whole catalog — one catalog, shown to all of them.

    Nothing is withheld on type or units. Each card carries its column's dtype, units
    and value range, so a mismatch is in front of the model when it decides; the
    router grades the pick afterwards (:func:`_routing`). Showing every field the same
    catalog is also what lets them share calls: the matcher splits only by size.
    """
    groups = merge_columns(catalog.columns) if catalog is not None else []
    tools = _answer_tools()
    cards = [tool_card(tool) for tool in tools] + [group_card(g) for g in groups]
    return _Matching(
        verdicts=matcher.match_many(requests=[(specs, cards)]),
        groups={group.ref: group for group in groups},
        tools={tool.name: tool.description or tool.name for tool in tools},
    )


def _settle_match(
    spec: FieldSpec,
    matching: _Matching,
    ranked: List[EvidenceRef],
    catalog: Optional[Catalog],
) -> Optional[FieldRouting]:
    """The matched column(s) or tool lead; BM25's ranking follows as the record.

    A matched group fans back out to every member, so a field answered by
    the same column in six tables routes to all six — ``candidates[0]`` is the first,
    and the compiler's bindings carry the rest.
    """
    decision = matching.verdicts.get(spec.path) or Verdict(
        choice=None, because="the column matcher returned no verdict for this field"
    )
    if decision.abstained:
        return None

    scores = {candidate_ref(c): c.score for c in ranked}
    choice = decision.choice
    if choice.startswith(TOOL_PREFIX):
        name = choice[len(TOOL_PREFIX):]
        chosen = [EvidenceRef(
            resource="", locator=name, kind="tool",
            snippet=matching.tools.get(name, name), score=scores.get(choice, 0.0),
        )]
    else:
        group = matching.groups.get(choice)
        members = group.members if group else ()
        chosen = [
            EvidenceRef(
                resource=c.resource, locator=c.name, kind="computed_column",
                snippet=f"{c.name}: {c.description or c.value_label or c.dtype}",
                score=scores.get(column_ref(c), 0.0),
            )
            for c in members
        ]
    if not chosen:
        return None

    taken = {candidate_ref(c) for c in chosen}
    candidates = chosen + [c for c in ranked if candidate_ref(c) not in taken]
    return _routing(spec, candidates, catalog, decision)


# ---------------------------------------------------------------------------
# Tier 2 with a judge: read each retrieved passage once, for all its fields
# ---------------------------------------------------------------------------


def _span_key(candidate: EvidenceRef) -> Tuple[str, Any]:
    locator = candidate.locator
    return candidate.resource, tuple(locator) if isinstance(locator, list) else locator


def _read(
    reader: PassageReader,
    pending: List[FieldSpec],
    spans: Dict[str, List[EvidenceRef]],
    passage,
) -> Dict[str, Dict[Tuple[str, Any], Verdict]]:
    """Read each retrieved passage once, for every field that retrieved it.

    Retrieval is still per field — a field is read only against the passages it
    ranked — but reading is per passage, so a passage six fields retrieved is one
    call, not six. Returns, per field, the verdict each of its passages gave.
    """
    order: List[Tuple[str, Any]] = []
    by_span: Dict[Tuple[str, Any], Tuple[EvidenceRef, List[FieldSpec]]] = {}
    for spec in pending:
        for candidate in spans[spec.path]:
            key = _span_key(candidate)
            if key not in by_span:
                order.append(key)
                by_span[key] = (candidate, [])
            by_span[key][1].append(spec)

    requests = [
        (by_span[key][1], passage_card(by_span[key][0], passage(by_span[key][0]) or ""))
        for key in order
    ]
    results = reader.read_many(requests=requests) if requests else []

    readings: Dict[str, Dict[Tuple[str, Any], Verdict]] = {s.path: {} for s in pending}
    for key, result in zip(order, results):
        for spec in by_span[key][1]:
            readings[spec.path][key] = result.get(spec.path) or Verdict(
                choice=None, because="the passage reader returned no verdict"
            )
    return readings


def _settle_read(
    spec: FieldSpec,
    ranked: List[EvidenceRef],
    readings: Dict[Tuple[str, Any], Verdict],
    catalog: Optional[Catalog],
    passage,
) -> Optional[FieldRouting]:
    """The best passage that states the field leads, narrowed to the sentence it quoted.

    Several passages can state one field — a README and a methods section both give
    the title. A located quote beats an unlocated one, then confidence, then the
    passage's own retrieval rank.
    """
    stated = [
        (rank, candidate, verdict)
        for rank, candidate in enumerate(ranked)
        if (verdict := readings.get(_span_key(candidate))) and not verdict.abstained
    ]
    if not stated:
        return None
    _, best, decision = max(
        stated,
        key=lambda item: (item[2].grounded is True, rank_of(item[2].confidence), -item[0]),
    )
    top, citation = _cite(best, decision.quote, passage)
    candidates = [top] + [c for c in ranked if c is not best]
    return _routing(spec, candidates, catalog, decision, citation)


def _bucket_of(top: EvidenceRef) -> str:
    """Which mechanism the rank-1 candidate implies."""
    if top.kind == "tool":
        return "tool"
    return "document" if top.kind == "quoted_span" else "column"


def _assurance(bucket: str, top: EvidenceRef, catalog: Optional[Catalog]) -> str:
    """Grade the routing from the top candidate.

    Two-hop for a computed column: the computation is recomputable (high), but the
    *interpretation* — that this column means what the field asks — is only as
    strong as the catalog resolution behind it, so the weaker hop wins. A retrieved
    span is quoted-only (low) until a verifier confirms it (a later milestone).
    """
    if bucket == "column" and catalog is not None:
        # Disambiguate by resource: a multi-table catalog can hold the same column
        # name in two tables, and the routed candidate names the one that won.
        column = catalog.find(top.locator, top.resource)
        return column.link_confidence if column else "low"
    if bucket == "document":
        return "low"
    return "high"


def route_fields(
    schema: Type[BaseModel],
    catalog: Optional[Catalog] = None,
    docs: Optional[List[Searchable]] = None,
    k: int = 3,
    matcher: Optional[ColumnMatcher] = None,
    reader: Optional[PassageReader] = None,
) -> FieldPlan:
    """Route every leaf field of ``schema`` to a source, producing a FieldPlan.

    ``catalog`` is the enriched column catalog (layer 3) for structural fields;
    ``docs`` are the document sources for narrative fields. A field that neither
    can answer is left ``unanswered`` — coverage, computed before any extraction.

    ``matcher`` and ``reader`` are the judges (layer 4b), one per tier. Without them
    rank 1 wins on BM25 score alone, which over-answers badly when the schema and the
    data were authored independently. Every routing is graded on type and unit fit
    (layer 4a), which lowers its assurance but never removes a candidate. See
    :mod:`src.router.type_fit`, :mod:`src.router.column_matcher` and
    :mod:`src.router.passage_reader`.
    """
    docs = docs or []
    specs = list(walk_schema(schema))
    passage = _passage_reader(docs)
    corpus = _document_corpus(docs, packed=reader is not None)
    judged = matcher is not None or reader is not None

    # Tier 1 — the structured corpus (tools + columns, one ranking). Without a matcher
    # the ranking decides; with one it is the record of what retrieval proposed, and
    # the matcher decides over the whole catalog.
    structured = {
        spec.path: _structured_candidates(spec.description or spec.path, catalog, k)
        for spec in specs
    }
    matching = _match(matcher, specs, catalog) if matcher is not None else None

    routings: Dict[str, FieldRouting] = {}
    pending: List[FieldSpec] = []
    for spec in specs:
        ranked = structured[spec.path]
        if matching is not None:
            settled = _settle_match(spec, matching, ranked, catalog)
        else:
            settled = _routing(spec, ranked, catalog) if ranked else None
        if settled is None:
            pending.append(spec)
        else:
            routings[spec.path] = settled

    # Tier 2 — the documents, for whatever tier 1 could not answer. A field reaches
    # here either because nothing structured matched or because the matcher rejected
    # what did; both mean the same thing to the document tier — the answer, if any,
    # is stated in prose.
    spans = {
        spec.path: _search_docs(
            corpus, spec.description or spec.path, k, offer_unscored=reader is not None
        )
        for spec in pending
    }
    readings = _read(reader, pending, spans, passage) if reader is not None else None
    for spec in pending:
        ranked = spans[spec.path]
        if readings is not None:
            settled = _settle_read(spec, ranked, readings[spec.path], catalog, passage)
        else:
            settled = _routing(spec, ranked, catalog) if ranked else None
        routings[spec.path] = settled or _unanswered(
            spec, structured[spec.path] + ranked, judged, catalog
        )

    # Emit in schema order, which the two-tier pass does not preserve on its own.
    return FieldPlan(
        schema_name=schema.__name__,
        routings={spec.path: routings[spec.path] for spec in specs},
    )
