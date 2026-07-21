"""Run-scoped evidence ledger for provenance tracing.

Every tool invocation is a *fact produced about a context*: a statistic, an
extent, a sample. To trace where a metadata value came from, those facts must be
captured at the moment they are produced, addressably, so a later step can cite
them and a verifier can replay them.

This module is that ledger. It is populated at the single tool boundary in
``tools/base._build_llm_facing_function`` — every tool funnels through there, so
one capture site instruments the whole tool surface (universal, tabular, and any
future modality) with no per-tool code.

Design notes
============

- **Keyed by ``context_key``.** Each run registers its context under a fresh key
  (see ``PlanExecutor.execute``), so evidence is namespaced per run without the
  ledger needing to know about run boundaries. ``get_evidence(key)`` returns just
  that run's facts — which is exactly what the final generator holds a handle to.
- **Captured after the call succeeds.** A tool that raises produced no fact, so
  it records nothing.
- **Not in the data path.** Recording is an append; nothing here can change a
  tool's return value. The ledger is read by provenance/eval code, never by the
  extraction itself.

The captured entry is deliberately the raw ``(tool, resource, args, result)``:
enough for a generator to cite (``source_ref``), for a human to read
(``describe``), and for a verifier to *replay* — re-invoke the same tool with the
same args and check the value still follows. Tabular evidence is a reproducible
computation, not a quotation, which is what makes it the most verifiable kind.

Each entry also carries ``used_by``: the callers that relied on it, in order.
A fact is produced once but requested by many (the orchestrator's inspect pass,
then several players across steps); deduplication would erase who asked, so the
callers are recorded as a list of uses rather than a single field. See ``Caller``
and ``attributed_to`` below.
"""

from __future__ import annotations

import contextvars
import itertools
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


_counter = itertools.count(1)
_LEDGER: Dict[str, List["EvidenceEntry"]] = {}


# ---------------------------------------------------------------------------
# Caller attribution — who asked, at which step
#
# A fact is deduplicated by (tool, resource, args): the orchestrator's inspect
# pass and several players may each request it, but it is produced once. So the
# caller cannot live on the fact as a single value — it is a *list of uses*. The
# current caller is carried ambiently in a contextvar rather than threaded
# through every tool signature, because the tool boundary is reached through
# LangChain's ``tool.invoke`` and the model's own tool-calling loop, neither of
# which we can add a parameter to. Each phase enters an ``attributed_to`` scope
# around the tools it fires; the capture site reads the scope on production and
# on every cache hit, so one fact records every step and player that used it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Caller:
    """Identity of whatever is firing tools right now."""

    agent: str                      # "orchestrator" | "player"
    role: Optional[str] = None      # player role key; None for the orchestrator
    step: Optional[int] = None      # plan step index; None before a plan exists
    phase: Optional[str] = None     # "inspect" | "survey" | "investigate"

    def as_use(self, *, cached: bool) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "role": self.role,
            "step": self.step,
            "phase": self.phase,
            "cached": cached,
        }


_UNKNOWN_USE = {
    "agent": "unknown",
    "role": None,
    "step": None,
    "phase": None,
}

_current_caller: contextvars.ContextVar[Optional[Caller]] = contextvars.ContextVar(
    "current_caller", default=None
)


@contextmanager
def attributed_to(caller: Caller) -> Iterator[None]:
    """Scope in which tool calls are attributed to ``caller``."""
    token = _current_caller.set(caller)
    try:
        yield
    finally:
        _current_caller.reset(token)


def current_caller() -> Optional[Caller]:
    """The caller tools are currently being fired on behalf of, if any."""
    return _current_caller.get()


def _use_of(caller: Optional[Caller], *, cached: bool) -> Dict[str, Any]:
    """A usage record for the current caller, or an 'unknown' one outside a scope."""
    if caller is None:
        return {**_UNKNOWN_USE, "cached": cached}
    return caller.as_use(cached=cached)


@dataclass
class EvidenceEntry:
    """One captured tool invocation: a fact produced about a context."""

    id: str
    context_key: str
    tool: str
    resource: Optional[str]
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    # Every caller that used this fact, in order. The first is the producer
    # (cached=False); the rest are cache hits (cached=True) — the same fact
    # reused by later steps and players without re-running the tool.
    used_by: List[Dict[str, Any]] = field(default_factory=list)

    def add_use(self, caller: Optional["Caller"], *, cached: bool) -> None:
        """Record that ``caller`` relied on this fact; ``cached`` if served from cache."""
        self.used_by.append(_use_of(caller, cached=cached))

    def describe(self) -> str:
        """Human/LLM-facing citation, e.g. ``get_temporal_extent(measurements, {'time_column': 'date'})``."""
        inside = []
        if self.resource:
            inside.append(self.resource)
        if self.args:
            inside.append(str(self.args))
        return f"{self.tool}({', '.join(inside)})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "context_key": self.context_key,
            "tool": self.tool,
            "resource": self.resource,
            "args": self.args,
            "result": self.result,
            "citation": self.describe(),
            "used_by": self.used_by,
        }


def record_evidence(
    *,
    context_key: str,
    tool: str,
    resource: Optional[str],
    args: Dict[str, Any],
    result: Any,
) -> "EvidenceEntry":
    """Append a captured invocation to the ledger and return the entry.

    The call that produced the fact is itself its first use, attributed to
    whichever caller scope is active (see :func:`attributed_to`).
    """
    entry = EvidenceEntry(
        id=f"ev_{next(_counter):06d}",
        context_key=context_key,
        tool=tool,
        resource=resource,
        args=dict(args),
        result=result,
    )
    entry.add_use(current_caller(), cached=False)
    _LEDGER.setdefault(context_key, []).append(entry)
    return entry


def record_reuse(entry: "EvidenceEntry") -> None:
    """Note that the current caller reused an already-captured fact (a cache hit)."""
    entry.add_use(current_caller(), cached=True)


def get_evidence(context_key: str) -> List[EvidenceEntry]:
    """Every fact captured for one run's context, in the order produced."""
    return list(_LEDGER.get(context_key, []))


def serialize_evidence(context_key: str) -> List[Dict[str, Any]]:
    """The run's evidence as plain dicts, for a prompt or an eval consumer."""
    return [e.to_dict() for e in get_evidence(context_key)]


def clear_evidence(context_key: Optional[str] = None) -> None:
    """Drop evidence for one context, or all of it when ``context_key`` is None."""
    if context_key is None:
        _LEDGER.clear()
    else:
        _LEDGER.pop(context_key, None)


# ---------------------------------------------------------------------------
# Attribution — from captured evidence to per-field provenance
#
# For tabular input, provenance is computed, not claimed. Each metadata value is
# matched against the facts the tools actually produced, so the trace is grounded
# in real tool output rather than a model's self-report. A value that traces to
# no captured fact is marked ``unverifiable`` — which is exactly the
# confabulation signal: the run asserted something nothing supports.
# ---------------------------------------------------------------------------

import json


@dataclass
class FieldProvenance:
    """Where one metadata field's value came from, if anywhere."""

    status: str  # "filled" | "unverifiable" | "not_present"
    source_type: Optional[str] = None      # "context_tool" for tabular tool output
    source_ref: Optional[str] = None       # the citing tool call, e.g. get_field_names(obs)
    transform: Optional[str] = None        # "verbatim" (exact) | "derived" (within a result)
    evidence_id: Optional[str] = None
    evidence: Optional[str] = None         # compact, self-contained snippet of the supporting fact

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "transform": self.transform,
            "evidence_id": self.evidence_id,
            "evidence": self.evidence,
        }


def _is_absent(value: Any) -> bool:
    # Explicit, so that a real 0 / False is not mistaken for a missing value.
    return value is None or value == "" or value == [] or value == {}


def _equalish(a: Any, b: Any) -> bool:
    return a == b or str(a) == str(b)


def _scalar_leaves(obj: Any):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _scalar_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _scalar_leaves(v)
    else:
        yield obj


def _serialize(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return str(obj)


def _filled(entry: "EvidenceEntry", transform: str) -> FieldProvenance:
    return FieldProvenance(
        status="filled",
        source_type="context_tool",
        source_ref=entry.describe(),
        transform=transform,
        evidence_id=entry.id,
        evidence=str(entry.result)[:200],
    )


def attribute_field(value: Any, entries: List["EvidenceEntry"]) -> FieldProvenance:
    """Trace one value to the tool call that produced it, if any.

    Conservative by design: prefer *no* attribution over a wrong one. Matching
    escalates from most to least specific, and the first match wins:

    1. the value equals a whole tool result → ``verbatim``;
    2. the value equals a scalar leaf inside a result → ``derived``;
    3. the value's string form appears within a result → ``derived``.

    Anything else is ``unverifiable`` — present but ungrounded.
    """
    if _is_absent(value):
        return FieldProvenance(status="not_present")

    for entry in entries:
        if _equalish(entry.result, value):
            return _filled(entry, "verbatim")

    for entry in entries:
        if any(_equalish(leaf, value) for leaf in _scalar_leaves(entry.result)):
            return _filled(entry, "derived")

    sval = str(value)
    if len(sval) >= 3:
        for entry in entries:
            if sval in _serialize(entry.result):
                return _filled(entry, "derived")

    return FieldProvenance(status="unverifiable")


def attribute_metadata(
    metadata: Dict[str, Any], entries: List["EvidenceEntry"]
) -> Dict[str, Dict[str, Any]]:
    """Per-field provenance sidecar for a metadata record, parallel to its values."""
    return {
        field_name: attribute_field(value, entries).to_dict()
        for field_name, value in metadata.items()
    }
