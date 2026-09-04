"""Layer 4b — the field reader: choose among routed candidates, or reject them all.

The router ranks lexically (BM25) and then reads its verdict off ``candidates[0]``.
Measured against hand labels on a real bundle, that verdict answers **88%** of a
schema whose true answerable rate is **24%** — it invents a source for 22 of the 25
fields nothing in the bundle can answer. The scores carry no usable reject signal:
raw BM25 is *anti*-correlated with correctness, because the highest-scoring hits are
exactly the confident lexical coincidences ("Fulton's condition factor" winning
``temperature`` on the word *condition*).

This module is the disposing half the design already promises but never had. A
reader is shown one field and the candidates retrieval surfaced — each with what
layer 3 resolved about it: meaning, units, dtype, value range, the citation it came
from — and answers with **one candidate or none**. Its most valuable answer is
*none*: over-answering, not mis-ranking, is where the accuracy goes.

Two boundaries worth keeping in mind.

**It re-ranks; it cannot retrieve.** A field whose true answer never entered the
candidate set is unreachable here no matter how good the reader is. Recall stays a
retrieval problem (see the embedding work in the field-router plan).

**A model proposes; code disposes.** Same discipline as layer 3: a choice naming a
candidate that was not offered is discarded, a quote that cannot be located in the
candidate's own material caps confidence at ``low``, and a failed or garbled call
abstains rather than crashes. The reader can only pick from what it was shown, and
can only be believed as far as it can cite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.context.base_context import EvidenceRef
from src.router.schema import FieldSpec

# The stable identity of a candidate, shared by the reader, the ground-truth sheet,
# and anything comparing a pick to a label. One string per answerable place, so a
# hand-written answer and a retrieved candidate compare with ``==``.
_TOOL_PREFIX = "tool::"
_DOC_PREFIX = "doc::"

#: Confidence values a reader may return, weakest first.
CONFIDENCE_ORDER = ("none", "low", "medium", "high")


def candidate_ref(candidate: EvidenceRef) -> str:
    """The stable reference string identifying what a candidate points at.

    Document spans collapse to their *document*: a reader cites a passage, and
    holding a pick to character offsets would measure the chunker, not the router.
    """
    if candidate.kind == "tool":
        return f"{_TOOL_PREFIX}{candidate.locator}"
    if candidate.kind == "quoted_span":
        return f"{_DOC_PREFIX}{candidate.resource}"
    return f"{candidate.resource}::{candidate.locator}"


@dataclass(frozen=True)
class Verdict:
    """What a reader concluded about one field's candidate set."""

    choice: Optional[str]                  # a candidate ref, or None to abstain
    because: str = ""
    quote: str = ""
    confidence: str = "none"               # high | medium | low | none
    grounded: bool = True                  # was ``quote`` found in the chosen material?

    @property
    def abstained(self) -> bool:
        return self.choice is None


def describe(candidate: EvidenceRef, catalog: Any = None) -> Dict[str, Any]:
    """Everything known about one candidate, as the flat card a reader judges.

    The units and the value range are the load-bearing parts. The two mistakes this
    bundle invites — reading ``EPOC::Duration`` (minutes of post-exercise recovery)
    as the condition's duration in days, and ``growth::condition`` (unitless, 0.89–
    1.22) as a temperature — are decidable from units and numbers and from nothing
    else. Layer 3 already computed both; this just puts them in front of the reader.
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
        card["text"] = candidate.snippet or ""
    return {key: value for key, value in card.items() if value not in (None, "")}


class FieldReader:
    """The seam: choose which candidate answers a field, or none of them.

    Implementations receive the field and the candidate cards and nothing else, so a
    reader never touches a catalog, a context, or an SDK. Return a :class:`Verdict`;
    abstention is a first-class answer, not a failure.
    """

    def choose(self, *, field: FieldSpec, cards: Sequence[Dict[str, Any]]) -> Verdict:
        raise NotImplementedError


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


class LLMFieldReader(FieldReader):
    """LLM-backed field reader — one call per field, abstention first-class.

    ``invoke`` is the only dependency: a callable ``prompt -> model text``, which
    keeps this free of any SDK and testable with a stub. Adapt a chat model with
    :meth:`from_chat_model`.

    The prompt asks the model to say what each candidate *measures* before choosing.
    Deciding first invites justification; describing first makes a unit mismatch
    visible to the model at the moment it matters. It is also told the base rate —
    most fields have no answer — because the default failure mode of a model handed
    five options is to pick one.
    """

    def __init__(self, invoke: Callable[[str], str]) -> None:
        self._invoke = invoke
        self._cache: Dict[str, Verdict] = {}

    @classmethod
    def from_chat_model(cls, model: Any) -> "LLMFieldReader":
        """Adapt a chat model exposing ``.invoke(prompt) -> message.content``."""
        return cls(lambda prompt: model.invoke(prompt).content)

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
        return Verdict(choice=None, because="no usable answer from the reader")

    raw = data.get("choice")
    because = str(data.get("because") or "").strip()
    if raw in (None, "", "null", "none", "NONE"):
        return Verdict(choice=None, because=because)

    card = next((c for c in cards if c["ref"] == str(raw).strip()), None)
    if card is None:
        # A ref that was never offered is a fabrication, not a pick.
        return Verdict(
            choice=None,
            because=f"reader named {raw!r}, which was not among the candidates",
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


def _locatable(quote: str, card: Dict[str, Any]) -> bool:
    """Is ``quote`` present in the card the reader was shown?

    Whitespace-normalized and case-folded, because a model reflows what it copies.
    An empty quote is not grounded — silence is not a citation.
    """
    if not quote:
        return False
    material = " ".join(str(v) for v in card.values()).casefold().split()
    needle = quote.casefold().split()
    if not needle:
        return False
    haystack = " ".join(material)
    return " ".join(needle) in haystack


def weaker(first: str, second: str) -> str:
    """The weaker of two confidence grades — the two-hop rule, kept in one place."""
    order = {name: index for index, name in enumerate(CONFIDENCE_ORDER)}
    return first if order.get(first, 0) <= order.get(second, 0) else second


def rerank(
    candidates: List[EvidenceRef], verdict: Verdict
) -> List[EvidenceRef]:
    """Reorder so the reader's choice is rank 1, keeping the rest in ranked order.

    Promoting rather than truncating is deliberate. Everything downstream reads
    ``candidates[0]`` — the bucket, the task's resource, the assurance — so promoting
    the chosen candidate makes those commitments follow a *judgment* instead of
    corpus iteration order, without the compiler having to change. The rejected
    candidates stay on the routing as the record of what was considered.
    """
    if verdict.abstained:
        return candidates
    chosen = [c for c in candidates if candidate_ref(c) == verdict.choice]
    return chosen + [c for c in candidates if candidate_ref(c) != verdict.choice]
