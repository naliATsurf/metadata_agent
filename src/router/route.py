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

- :mod:`src.router.veto` (4a, deterministic) drops candidates that *cannot* answer
  the field on declared type or units — a name field against an integer column, a
  whole-number field against fractional values. Permanent, so deliberately narrow.
- :mod:`src.router.judge` (4b, optional, a model) adjudicates what survives and
  may reject all of it. Its most valuable answer is *none*.

With a judge, ``candidates[0]`` is the judge's pick rather than BM25's, so the
bucket and (downstream) the task's resources follow a judgement instead of corpus
iteration order. The ordering is the whole seam; no consumer changes.

Routing runs as **two passes, not field by field**: the structured tier for every
field, adjudicated in one batch, then the document tier for whatever tier 1 could
not answer, adjudicated in a second. A judge is a network call, and a schema is
dozens of fields, so the batch boundary is what keeps a routing pass affordable.

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
from src.router.judge import CandidateJudge, Verdict, describe, promote, weaker
from src.router.veto import apply_veto
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
    # Populated when a candidate judge adjudicated the candidate set (layer 4b):
    judge_choice: Optional[str] = None     # the ref it picked, None when it abstained
    judge_note: Optional[str] = None       # why it picked that one, or why none
    judge_quote: Optional[str] = None      # the sentence it cited from that candidate
    judge_grounded: Optional[bool] = None  # could its quote be found in the material?
    citation: Optional[str] = None         # resource#start-end of the cited sentence
    vetoed: List[str] = field(default_factory=list)   # candidates ruled out, and why
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
            "vetoed": self.vetoed,
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
    ``packed`` is therefore the judge's presence. With a judge, whole passages go out
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


def _adjudicate(
    judge: Optional[CandidateJudge],
    items: List[Tuple[FieldSpec, List[EvidenceRef]]],
    catalog: Optional[Catalog],
    passage,
) -> Dict[str, Verdict]:
    """Put one whole tier's candidate sets to the judge, in a single pass.

    Batched rather than field-by-field because a judge is a network call and a
    routing pass is dozens of them: handing the judge the whole tier lets it group
    fields offered identical candidates and issue the calls concurrently. Fields the
    retrieval left empty are not asked about at all.
    """
    if judge is None:
        return {}
    requests = [
        (spec, [describe(c, catalog, passage(c)) for c in candidates])
        for spec, candidates in items
        if candidates
    ]
    return judge.choose_many(requests=requests) if requests else {}


def _settle(
    spec: FieldSpec,
    candidates: List[EvidenceRef],
    verdict: Optional[Verdict],
    catalog: Optional[Catalog],
    judge: Optional[CandidateJudge],
    vetoed: List[str],
    passage=None,
) -> Optional[FieldRouting]:
    """Build the routing for one tier's candidates, or None if it cannot answer.

    Without a judge this is the historical behaviour: rank 1 wins, and the bucket,
    the assurance, and (downstream) the task's resources are all read off it. With
    one, rank 1 is the judge's pick rather than BM25's, so those same commitments
    follow a judgement — the ordering is the seam, which is why no consumer changes.
    """
    if not candidates:
        return None

    decision = Verdict(choice=None)
    if judge is not None:
        decision = verdict or Verdict(
            choice=None, because="the judge returned no verdict for this field"
        )
        if decision.abstained:
            return None
        candidates = promote(candidates, decision, passage)

    top = candidates[0]
    citation = None
    if judge is not None and decision.quote and top.kind == "quoted_span":
        top, citation = _cite(top, decision.quote, passage)
        candidates = [top] + candidates[1:]
    bucket = _bucket_of(top)
    assurance = _assurance(bucket, top, catalog)
    if judge is not None:
        # Two hops again: the judge's confidence in the *match*, and the catalog's
        # in the column's *meaning*. The routing is only as strong as the weaker.
        assurance = weaker(decision.confidence, assurance)
    return FieldRouting(
        field_path=spec.path, query=spec.description or spec.path, bucket=bucket,
        candidates=candidates, assurance=assurance,
        judge_choice=decision.choice,
        judge_note=decision.because or None,
        # The quote is the sentence the judge cited, verified present in the candidate
        # it chose. For a document field it is the nearest thing to the answer the
        # router ever holds, and re-deriving it downstream means re-reading the whole
        # document — so it travels on the routing rather than being recomputed.
        judge_quote=decision.quote or None,
        judge_grounded=decision.grounded if judge is not None else None,
        citation=citation,
        vetoed=vetoed,
    )


def _unanswered(
    spec: FieldSpec,
    rejected: List[EvidenceRef],
    judge: Optional[CandidateJudge],
    vetoed: List[str],
) -> FieldRouting:
    """A field no tier could answer — coverage, computed before extraction runs."""
    return FieldRouting(
        field_path=spec.path, query=spec.description or spec.path,
        bucket="unanswered",
        # With a judge the rejected set is the record of what was considered and
        # refused; without one an empty list keeps the historical shape.
        candidates=rejected if judge is not None else [],
        assurance="none", status="unanswered",
        judge_note="judge found no candidate that answers this field"
        if judge is not None and rejected
        else None,
        vetoed=vetoed,
    )


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
    judge: Optional[CandidateJudge] = None,
    veto: bool = True,
) -> FieldPlan:
    """Route every leaf field of ``schema`` to a source, producing a FieldPlan.

    ``catalog`` is the enriched column catalog (layer 3) for structural fields;
    ``docs`` are the document sources for narrative fields. A field that neither
    can answer is left ``unanswered`` — coverage, computed before any extraction.

    ``veto`` applies the deterministic type/unit filter (layer 4a) before anything
    reads the candidates; ``judge`` is the optional candidate judge (layer 4b). Without
    either, rank 1 wins on BM25 score alone, which over-answers badly when the schema
    and the data were authored independently. See :mod:`src.router.veto` and
    :mod:`src.router.judge`.
    """
    docs = docs or []
    specs = list(walk_schema(schema))
    passage = _passage_reader(docs)
    corpus = _document_corpus(docs, packed=judge is not None)

    # Tier 1 — the structured corpus (tools + columns, one ranking), vetoed on type
    # and units before anything reads it. A ranking cannot tell a genus name from a
    # pH treatment level; a dtype can (layer 4a).
    structured: Dict[str, List[EvidenceRef]] = {}
    vetoes: Dict[str, List[str]] = {}
    for spec in specs:
        candidates = _structured_candidates(spec.description or spec.path, catalog, k)
        reasons: List[str] = []
        if veto and candidates:
            candidates, reasons = apply_veto(spec, candidates, catalog)
        structured[spec.path], vetoes[spec.path] = candidates, reasons

    verdicts = _adjudicate(
        judge, [(s, structured[s.path]) for s in specs], catalog, passage
    )

    routings: Dict[str, FieldRouting] = {}
    pending: List[FieldSpec] = []
    for spec in specs:
        settled = _settle(
            spec, structured[spec.path], verdicts.get(spec.path),
            catalog, judge, vetoes[spec.path], passage,
        )
        if settled is None:
            pending.append(spec)
        else:
            routings[spec.path] = settled

    # Tier 2 — the documents, for whatever tier 1 could not answer. A field reaches
    # here either because nothing structured matched or because the judge rejected
    # what did; both mean the same thing to the document tier — the answer, if any,
    # is stated in prose — so a judge that dismissed a set of lexical coincidences
    # still gets to judge the narrative sources.
    spans = {
        spec.path: _search_docs(
            corpus, spec.description or spec.path, k, offer_unscored=judge is not None
        )
        for spec in pending
    }
    span_verdicts = _adjudicate(
        judge, [(s, spans[s.path]) for s in pending], catalog, passage
    )
    for spec in pending:
        routings[spec.path] = _settle(
            spec, spans[spec.path], span_verdicts.get(spec.path),
            catalog, judge, vetoes[spec.path], passage,
        ) or _unanswered(
            spec, structured[spec.path] + spans[spec.path], judge, vetoes[spec.path]
        )

    # Emit in schema order, which the two-tier pass does not preserve on its own.
    return FieldPlan(
        schema_name=schema.__name__,
        routings={spec.path: routings[spec.path] for spec in specs},
    )
