"""Layer 4b — the judges: decide what answers each field, or that nothing does.

The router ranks lexically (BM25). Measured against hand labels on a real bundle,
taking rank 1 as the answer fills **88%** of a schema whose true answerable rate is
**24%** — it invents a source for 22 of the 25 fields nothing in the bundle can
answer. The scores carry no usable reject signal: raw BM25 is *anti*-correlated with
correctness, because the highest-scoring hits are exactly the confident lexical
coincidences ("Fulton's condition factor" winning ``temperature`` on *condition*).
A model decides instead, and its most valuable answer is *none*.

**Three judges, because tools, columns and prose pose different questions.**

- :mod:`src.router.tool_matcher` — *is this field computed?* A tool is an operation,
  not a place: whether a row count answers ``sample_size`` depends on the schema and
  the tool, not on the bundle. So the tool matcher sees no data, and its answers can
  be cached across bundles.
- :mod:`src.router.column_matcher` — *matching*. The catalog is fixed and identical
  for every field, the evidence is structured (units, value range, dtype), and seeing
  every field at once helps: a model that sees ``temperature`` and ``oxygen`` beside
  each other is better placed to see that ``p50`` is a fish's response, not a tank
  condition. So the catalog is shown once and all fields are matched against it —
  together with the table or columns each chosen tool must be run on.
- :mod:`src.router.passage_reader` — *reading*. A passage either states a field's
  value or it does not, and the sentence that states it *is* the evidence: locating
  the quote is what turns "somewhere in this file" into a citation. So each passage
  is read once for every field that retrieved it.

What they share lives here: the ref vocabulary a pick and a hand label compare in,
the :class:`Verdict`, the confidence grades, the pick referee, and the concurrent
dispatch.

**A model proposes; code disposes.** No judge is believed further than it can be
checked: a ref that was not offered is discarded, a quote that cannot be located caps
confidence at ``low``, and a failed or garbled call abstains rather than crashes.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any, Callable, Collection, List, Optional, Sequence, Tuple, TypeVar

from src.context.base_context import EvidenceRef

# The stable identity of a candidate, shared by the judges, the ground-truth sheet,
# and anything comparing a pick to a label. One string per answerable place, so a
# hand-written answer and a retrieved candidate compare with ``==``.
TOOL_PREFIX = "tool::"
DOC_PREFIX = "doc::"
#: A whole table, as the column matcher offers it for a tool's ``resource`` argument.
TABLE_PREFIX = "table::"

#: Confidence values a judge may return, weakest first.
CONFIDENCE_ORDER = ("none", "low", "medium", "high")


def candidate_ref(candidate: EvidenceRef) -> str:
    """The stable reference string identifying what a candidate points at.

    Document spans collapse to their *document*: a judge cites a passage, and
    holding a pick to character offsets would measure the chunker, not the router.
    """
    if candidate.kind == "tool":
        return f"{TOOL_PREFIX}{candidate.locator}"
    if candidate.kind == "quoted_span":
        return f"{DOC_PREFIX}{candidate.resource}"
    return f"{candidate.resource}::{candidate.locator}"


@dataclass(frozen=True)
class Verdict:
    """What a judge concluded about one field."""

    choice: Optional[str]                  # a candidate ref, or None to abstain
    because: str = ""
    #: Every sentence cited as stating the value, in the order given.
    quotes: Tuple[str, ...] = ()
    confidence: str = "none"               # high | medium | low | none
    #: Was *every* quote found in the material? ``None`` where a quote is not the
    #: evidence at all — a column is matched on its card, not cited from it.
    grounded: Optional[bool] = None

    @property
    def abstained(self) -> bool:
        return self.choice is None


def confidence_of(raw: Any) -> str:
    """A model's stated confidence, normalized; anything unrecognised reads ``low``."""
    value = str(raw or "low").strip().lower()
    return value if value in CONFIDENCE_ORDER else "low"


def weaker(first: str, second: str) -> str:
    """The weaker of two confidence grades — the two-hop rule, kept in one place."""
    order = {name: index for index, name in enumerate(CONFIDENCE_ORDER)}
    return first if order.get(first, 0) <= order.get(second, 0) else second


def rank_of(confidence: str) -> int:
    """A confidence grade's position, for ordering verdicts strongest first."""
    return CONFIDENCE_ORDER.index(confidence) if confidence in CONFIDENCE_ORDER else 0


def contains(haystack: str, needle: str) -> bool:
    """Whitespace-normalized, case-folded containment — a model reflows what it copies."""
    if not needle:
        return False
    return " ".join(needle.casefold().split()) in " ".join(haystack.casefold().split())


def pick(data: Any, shown: Collection[str], judge: str) -> Verdict:
    """Validate one field's ``{choice, because, confidence}`` against the refs shown.

    A ref the model composed — or took from a call that did not show it — is discarded
    rather than trusted. Silence about a field is not a pick.
    """
    if not isinstance(data, dict):
        return Verdict(choice=None, because=f"no usable answer from the {judge}")
    raw = data.get("choice")
    because = str(data.get("because") or "").strip()
    if raw in (None, "", "null", "none", "NONE"):
        return Verdict(choice=None, because=because)
    ref = str(raw).strip()
    if ref not in shown:
        return Verdict(
            choice=None, because=f"{judge} named {raw!r}, which was not in the catalog shown"
        )
    return Verdict(choice=ref, because=because, confidence=confidence_of(data.get("confidence")))


def json_object(text: Any) -> Optional[dict]:
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


def ask(invoke: Callable[[str], str], prompt: str) -> dict:
    """One call, parsed. A raising or garbled call answers ``{}``: every field abstains."""
    try:
        return json_object(invoke(prompt)) or {}
    except Exception:                      # noqa: BLE001 — a failed call abstains
        return {}


T = TypeVar("T")
R = TypeVar("R")


def dispatch(run: Callable[[T], R], calls: Sequence[T], max_workers: int) -> List[R]:
    """Issue ``calls`` ``max_workers`` at a time, results in input order.

    Each runs in a copy of the caller's context, so whatever the caller scoped around
    the judge (an LLM call count, a tracer) sees these calls too.
    """
    if max_workers <= 1 or len(calls) <= 1:
        return [run(call) for call in calls]
    contexts = [copy_context() for _ in calls]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(
            lambda context, call: context.run(run, call), contexts, calls
        ))


def in_groups(items: Sequence[T], size: int) -> List[List[T]]:
    """Consecutive runs of at most ``size`` items, as even as possible.

    27 fields with a cap of 20 make 14 + 13, not 20 + 7: the cap bounds how many fields
    one call is asked about, and a lopsided split gives one call nearly all of them.
    """
    if not items:
        return []
    count = -(-len(items) // max(1, size))
    base, extra = divmod(len(items), count)
    groups, start = [], 0
    for index in range(count):
        end = start + base + (1 if index < extra else 0)
        groups.append(list(items[start:end]))
        start = end
    return groups


def field_lines(fields: Sequence[Any]) -> str:
    """The FIELDS block the judges' prompts list their fields in."""
    return "\n".join(
        f"- {f.path} ({f.type}) — {f.description or f.path}" for f in fields
    )
