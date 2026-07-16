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
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_counter = itertools.count(1)
_LEDGER: Dict[str, List["EvidenceEntry"]] = {}


@dataclass
class EvidenceEntry:
    """One captured tool invocation: a fact produced about a context."""

    id: str
    context_key: str
    tool: str
    resource: Optional[str]
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None

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
        }


def record_evidence(
    *,
    context_key: str,
    tool: str,
    resource: Optional[str],
    args: Dict[str, Any],
    result: Any,
) -> str:
    """Append a captured invocation to the ledger and return its evidence id."""
    entry = EvidenceEntry(
        id=f"ev_{next(_counter):06d}",
        context_key=context_key,
        tool=tool,
        resource=resource,
        args=dict(args),
        result=result,
    )
    _LEDGER.setdefault(context_key, []).append(entry)
    return entry.id


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
