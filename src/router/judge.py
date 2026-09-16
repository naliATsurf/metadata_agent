"""Layer 4b — the candidate judge: choose among routed candidates, or reject them all.

The router ranks lexically (BM25) and then reads its verdict off ``candidates[0]``.
Measured against hand labels on a real bundle, that verdict answers **88%** of a
schema whose true answerable rate is **24%** — it invents a source for 22 of the 25
fields nothing in the bundle can answer. The scores carry no usable reject signal:
raw BM25 is *anti*-correlated with correctness, because the highest-scoring hits are
exactly the confident lexical coincidences ("Fulton's condition factor" winning
``temperature`` on the word *condition*).

This module is the disposing half the design already promises but never had. A
judge is shown one field and the candidates retrieval surfaced — each with what
layer 3 resolved about it: meaning, units, dtype, value range, the citation it came
from — and answers with **one candidate or none**. Its most valuable answer is
*none*: over-answering, not mis-ranking, is where the accuracy goes.

Three boundaries worth keeping in mind.

**It re-ranks; it cannot retrieve.** A field whose true answer never entered the
candidate set is unreachable here no matter how good the judge is. Recall stays a
retrieval problem (see the embedding work in the field-router plan).

**A model proposes; code disposes.** Same discipline as layer 3: a choice naming a
candidate that was not offered is discarded, a quote that cannot be located in the
candidate's own material caps confidence at ``low``, and a failed or garbled call
abstains rather than crashes. The judge can only pick from what it was shown, and
can only be believed as far as it can cite.

**One field is the unit of judgement, not the unit of round-trip.** Fields offered
an identical candidate list can share a call, and the calls can be issued
concurrently (see :class:`LLMCandidateJudge`), because a schema is dozens of fields and
a judge is a network hop. Judged together, though, fields stop being independent —
a model shown one passage and nine fields tends to *distribute* answers among them —
so grouping is defeasible and worth measuring against a labeled sheet rather than
assumed free.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.context.base_context import EvidenceRef
from src.router.schema import FieldSpec

# The stable identity of a candidate, shared by the judge, the ground-truth sheet,
# and anything comparing a pick to a label. One string per answerable place, so a
# hand-written answer and a retrieved candidate compare with ``==``.
_TOOL_PREFIX = "tool::"
_DOC_PREFIX = "doc::"

#: Confidence values a judge may return, weakest first.
CONFIDENCE_ORDER = ("none", "low", "medium", "high")


def candidate_ref(candidate: EvidenceRef) -> str:
    """The stable reference string identifying what a candidate points at.

    Document spans collapse to their *document*: a judge cites a passage, and
    holding a pick to character offsets would measure the chunker, not the router.
    """
    if candidate.kind == "tool":
        return f"{_TOOL_PREFIX}{candidate.locator}"
    if candidate.kind == "quoted_span":
        return f"{_DOC_PREFIX}{candidate.resource}"
    return f"{candidate.resource}::{candidate.locator}"


@dataclass(frozen=True)
class Verdict:
    """What a judge concluded about one field's candidate set."""

    choice: Optional[str]                  # a candidate ref, or None to abstain
    because: str = ""
    quote: str = ""
    confidence: str = "none"               # high | medium | low | none
    grounded: bool = True                  # was ``quote`` found in the chosen material?

    @property
    def abstained(self) -> bool:
        return self.choice is None


def describe(
    candidate: EvidenceRef, catalog: Any = None, passage: Optional[str] = None
) -> Dict[str, Any]:
    """Everything known about one candidate, as the flat card a judge rules on.

    The units and the value range are the load-bearing parts. The two mistakes this
    bundle invites — reading ``EPOC::Duration`` (minutes of post-exercise recovery)
    as the condition's duration in days, and ``growth::condition`` (unitless, 0.89–
    1.22) as a temperature — are decidable from units and numbers and from nothing
    else. Layer 3 already computed both; this just puts them in front of the judge.

    ``passage`` is the **full text a span candidate points at**, and for a document
    candidate it is the whole card. Falling back to ``snippet`` is a last resort: a
    snippet is a 200-character *preview* by design, a retrieved chunk is routinely ten
    times that, and the sentence that answers the field is as likely to sit past the
    cut as before it. A judge shown a preview abstains for want of evidence that was
    retrieved and then withheld — indistinguishable, from the outside, from a judge
    that read the passage and found nothing in it.
    """
    card: Dict[str, Any] = {
        "ref": candidate_ref(candidate),
        "kind": {"tool": "tool", "quoted_span": "document"}.get(
            candidate.kind, "column"
        ),
    }
    column = None
    if catalog is not None and candidate.kind not in ("tool", "quoted_span"):
        column = catalog.find(candidate.locator, candidate.resource)

    if column is not None:
        card.update(
            table=column.resource,
            column=column.name,
            meaning=column.description,
            units=column.units,
            dtype=column.dtype,
            value_prior=column.value_label,
        )
        if column.value_range:
            low, high = column.value_range
            card["value_range"] = f"{low:g} to {high:g}"
        if column.link_evidence:
            card["meaning_cited_from"] = column.link_evidence
        if column.link_quote:
            card["source_text"] = column.link_quote
    else:
        card["text"] = passage or candidate.snippet or ""
    return {key: value for key, value in card.items() if value not in (None, "")}


#: One field and the candidate cards offered for it.
Request = Tuple[FieldSpec, Sequence[Dict[str, Any]]]


class CandidateJudge:
    """The seam: choose which candidate answers a field, or none of them.

    Implementations receive the field and the candidate cards and nothing else, so a
    judge never touches a catalog, a context, or an SDK. Return a :class:`Verdict`;
    abstention is a first-class answer, not a failure.

    Two entrypoints, the same shape as :class:`~src.router.catalog.ProseReader`.
    :meth:`choose` handles one field; :meth:`choose_many` handles a whole routing
    pass and is what the router actually calls. Implement ``choose`` and inherit the
    default ``choose_many`` that loops it, or override ``choose_many`` for a backend
    where round-trips are the expensive part.
    """

    def choose(self, *, field: FieldSpec, cards: Sequence[Dict[str, Any]]) -> Verdict:
        raise NotImplementedError

    def choose_many(self, *, requests: Sequence[Request]) -> Dict[str, Verdict]:
        """Adjudicate many fields, keyed by field path. Default: loop :meth:`choose`."""
        return {
            spec.path: self.choose(field=spec, cards=cards) for spec, cards in requests
        }


_INSTRUCTION = (
    "You decide which data source, if any, answers one metadata field.\n\n"
    "FIELD asks for: {asks}\n"
    "Field name: {path}\nExpected type: {type}\n\n"
    "CANDIDATES (retrieved by keyword search, so most are coincidences):\n"
    "{cards}\n\n"
    "A candidate answers the field only if it holds *the quantity the field asks "
    "for*. Sharing a word is not enough. Check the units and the value range: a "
    "field wanting a duration in days is not answered by a column of minutes, and a "
    "field wanting a temperature is not answered by a unitless index ranging 0.9 to "
    "1.2. A column measuring the subject's response is not the experimental "
    "condition it was measured under.\n\n"
    "Most fields in a typical dataset have NO answer, because the schema and the "
    "data were written by different people for different purposes. Answering with "
    "null is the normal, expected outcome — a wrong source is far worse than none.\n\n"
    'Return ONE JSON object: {{"measures": <one clause per candidate ref, saying '
    'what it actually holds>, "choice": <the ref of the candidate that answers the '
    'field, or null>, "because": <why that candidate does or why none does>, '
    '"quote": <text copied verbatim from the chosen candidate\'s card supporting '
    'the choice; "" when choice is null>, "confidence": "high"|"medium"|"low"}}\n'
    "No prose outside the JSON, no code fence."
)


_BATCH_INSTRUCTION = (
    "You decide, for each of several metadata fields, which data source answers it "
    "— if any. Every field below was matched against the SAME candidates.\n\n"
    "CANDIDATES (retrieved by keyword search, so most are coincidences):\n"
    "{cards}\n\n"
    "FIELDS:\n{fields}\n\n"
    "A candidate answers a field only if it holds *the quantity that field asks "
    "for*. Sharing a word is not enough. Check the units and the value range: a "
    "field wanting a duration in days is not answered by a column of minutes, and a "
    "field wanting a temperature is not answered by a unitless index ranging 0.9 to "
    "1.2. A column measuring the subject's response is not the experimental "
    "condition it was measured under.\n\n"
    "Most fields in a typical dataset have NO answer, because the schema and the "
    "data were written by different people for different purposes. Answering with "
    "null is the normal, expected outcome — a wrong source is far worse than none.\n\n"
    "The fields are INDEPENDENT. Judge each one on its own against the candidates. "
    "The same candidate may answer several fields, or none at all. Do not assume a "
    "candidate must answer something, and do not spread answers across fields to "
    "use the candidates up — it is entirely normal for every field here to be "
    "null.\n\n"
    'Return ONE JSON object mapping each field name to {{"choice": <the ref of the '
    'candidate that answers it, or null>, "because": <why>, "quote": <text copied '
    'verbatim from the chosen candidate\'s card; "" when choice is null>, '
    '"confidence": "high"|"medium"|"low"}}\n'
    "No prose outside the JSON, no code fence."
)


class LLMCandidateJudge(CandidateJudge):
    """LLM-backed candidate judge — abstention first-class.

    ``invoke`` is the only dependency: a callable ``prompt -> model text``, which
    keeps this free of any SDK and testable with a stub. Adapt a chat model with
    :meth:`from_chat_model`.

    The prompt asks the model to say what each candidate *measures* before choosing.
    Deciding first invites justification; describing first makes a unit mismatch
    visible to the model at the moment it matters. It is also told the base rate —
    most fields have no answer — because the default failure mode of a model handed
    five options is to pick one.

    **Round-trips.** ``choose_many`` groups fields that were offered an *identical*
    candidate set into one call, so a slow endpoint is asked fewer times. Grouping
    only identical sets keeps the prompt unambiguous — there is one candidate list,
    not a per-field mapping the model has to track. ``max_workers`` then issues those
    calls concurrently, which is usually the larger win: a self-hosted server batches
    concurrent requests internally, so latency falls without bundling more fields
    into one prompt.

    The trade-off in grouping is real and worth naming: judged together, fields stop
    being independent, and a model shown one passage and nine fields tends to
    *distribute* answers among them — the same accept-bias the design fights. The
    prompt says so explicitly, and ``batch=False`` turns grouping off so the two can
    be compared on a labeled sheet.
    """

    def __init__(
        self,
        invoke: Callable[[str], str],
        *,
        batch: bool = True,
        max_workers: int = 1,
    ) -> None:
        self._invoke = invoke
        self._cache: Dict[str, Verdict] = {}          # single-field verdicts
        self._group_cache: Dict[str, Dict[str, Verdict]] = {}   # grouped verdicts
        self._batch = batch
        self._max_workers = max(1, max_workers)

    @classmethod
    def from_chat_model(
        cls, model: Any, *, batch: bool = True, max_workers: int = 1
    ) -> "LLMCandidateJudge":
        """Adapt a chat model exposing ``.invoke(prompt) -> message.content``."""
        return cls(
            lambda prompt: model.invoke(prompt).content,
            batch=batch, max_workers=max_workers,
        )

    # -- batched entrypoint --------------------------------------------------

    def choose_many(self, *, requests: Sequence[Request]) -> Dict[str, Verdict]:
        """Adjudicate every field in one pass, grouping and parallelising the calls."""
        groups = _group_by_candidates(requests) if self._batch else [
            [item] for item in requests
        ]
        if not groups:
            return {}
        if self._max_workers == 1 or len(groups) == 1:
            results = [self._run_group(group) for group in groups]
        else:
            # Each group runs in a copy of the caller's context, so whatever the caller
            # scoped around the judge (an LLM call count, a tracer) sees these calls.
            contexts = [copy_context() for _ in groups]
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                results = list(pool.map(
                    lambda context, group: context.run(self._run_group, group),
                    contexts, groups,
                ))
        merged: Dict[str, Verdict] = {}
        for result in results:
            merged.update(result)
        return merged

    def _run_group(self, group: Sequence[Request]) -> Dict[str, Verdict]:
        """One call for a set of fields that share a candidate list."""
        if len(group) == 1:
            spec, cards = group[0]
            return {spec.path: self.choose(field=spec, cards=cards)}

        cards = group[0][1]
        specs = [spec for spec, _ in group]
        key = json.dumps(
            [[s.path for s in specs], [c["ref"] for c in cards]], sort_keys=True
        )
        if key in self._group_cache:
            return dict(self._group_cache[key])

        prompt = _BATCH_INSTRUCTION.format(
            cards=json.dumps(list(cards), indent=2, default=str),
            fields="\n".join(
                f"- {s.path} ({s.type}) — {s.description or s.path}" for s in specs
            ),
        )
        try:
            data = _json_object(self._invoke(prompt)) or {}
        except Exception:
            data = {}                      # a failed call abstains every field in it
        verdicts = {
            spec.path: _referee(
                data.get(spec.path) if isinstance(data.get(spec.path), dict) else None,
                cards,
            )
            for spec in specs
        }
        self._group_cache[key] = verdicts
        return dict(verdicts)


    def choose(self, *, field: FieldSpec, cards: Sequence[Dict[str, Any]]) -> Verdict:
        if not cards:
            return Verdict(choice=None, because="no candidates were retrieved")

        key = json.dumps([field.path, [c["ref"] for c in cards]], sort_keys=True)
        if key in self._cache:
            return self._cache[key]

        prompt = _INSTRUCTION.format(
            asks=field.description or field.path,
            path=field.path,
            type=field.type,
            cards=json.dumps(list(cards), indent=2, default=str),
        )
        try:
            data = _json_object(self._invoke(prompt))
        except Exception:
            data = None                    # a failed call abstains, never crashes
        verdict = _referee(data, cards)
        self._cache[key] = verdict
        return verdict


def _group_by_candidates(requests: Sequence[Request]) -> List[List[Request]]:
    """Bucket requests whose candidate lists are identical, preserving input order.

    Only *identical* sets group. Two fields offered overlapping-but-different
    candidates would need a per-field mapping inside the prompt, which is exactly
    the ambiguity that makes a bundled answer worse than several small ones.
    """
    buckets: Dict[Tuple[str, ...], List[Request]] = {}
    for spec, cards in requests:
        buckets.setdefault(tuple(c["ref"] for c in cards), []).append((spec, cards))
    return list(buckets.values())


def _json_object(text: Any) -> Optional[dict]:
    """Best-effort parse of one JSON object (tolerates a fence or stray prose)."""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _referee(data: Optional[dict], cards: Sequence[Dict[str, Any]]) -> Verdict:
    """Validate a model's answer against what it was actually shown.

    Two checks, both deterministic. The choice must name a candidate that was
    offered — a ref the model composed is discarded rather than trusted. And the
    quote must be locatable in that candidate's own card; a pick the model cannot
    cite is kept but capped at ``low``, the same grading layer 3 applies to a read
    whose quote does not appear in the passage.
    """
    if not data:
        return Verdict(choice=None, because="no usable answer from the judge")

    raw = data.get("choice")
    because = str(data.get("because") or "").strip()
    if raw in (None, "", "null", "none", "NONE"):
        return Verdict(choice=None, because=because)

    card = next((c for c in cards if c["ref"] == str(raw).strip()), None)
    if card is None:
        # A ref that was never offered is a fabrication, not a pick.
        return Verdict(
            choice=None,
            because=f"judge named {raw!r}, which was not among the candidates",
        )

    quote = str(data.get("quote") or "").strip()
    grounded = _locatable(quote, card)
    confidence = str(data.get("confidence") or "low").strip().lower()
    if confidence not in CONFIDENCE_ORDER:
        confidence = "low"
    if not grounded:
        confidence = "low"
    return Verdict(
        choice=card["ref"], because=because, quote=quote,
        confidence=confidence, grounded=grounded,
    )


def _contains(haystack: str, needle: str) -> bool:
    """Whitespace-normalized, case-folded containment — a model reflows what it copies."""
    if not needle:
        return False
    return " ".join(needle.casefold().split()) in " ".join(haystack.casefold().split())


def _locatable(quote: str, card: Dict[str, Any]) -> bool:
    """Is ``quote`` present in the card the judge was shown?

    An empty quote is not grounded — silence is not a citation.
    """
    return _contains(" ".join(str(value) for value in card.values()), quote)


def weaker(first: str, second: str) -> str:
    """The weaker of two confidence grades — the two-hop rule, kept in one place."""
    order = {name: index for index, name in enumerate(CONFIDENCE_ORDER)}
    return first if order.get(first, 0) <= order.get(second, 0) else second


def promote(
    candidates: List[EvidenceRef],
    verdict: Verdict,
    passage: Optional[Callable[[EvidenceRef], Optional[str]]] = None,
) -> List[EvidenceRef]:
    """Reorder so the judge's choice is rank 1, keeping the rest in ranked order.

    Promoting rather than truncating is deliberate. Everything downstream reads
    ``candidates[0]`` — the bucket, the task's resource, the assurance — so promoting
    the chosen candidate makes those commitments follow a *judgment* instead of
    corpus iteration order, without the compiler having to change. The rejected
    candidates stay on the routing as the record of what was considered.

    **Every span of one document shares its ref** (:func:`candidate_ref` collapses
    ``quoted_span`` to ``doc::<resource>``), so a verdict naming a document cannot on
    its own say *which* passage of it was meant, and rank 1 would fall back to BM25
    order — discarding the span-level judgement just paid for. The judge's own quote
    disposes: given ``passage``, the span it actually cited leads.
    """
    if verdict.abstained:
        return candidates
    chosen = [c for c in candidates if candidate_ref(c) == verdict.choice]
    rest = [c for c in candidates if candidate_ref(c) != verdict.choice]
    if len(chosen) > 1 and passage is not None and verdict.quote:
        chosen.sort(key=lambda c: not _contains(passage(c) or "", verdict.quote))
    return chosen + rest
