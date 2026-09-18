"""The field-driven path end to end: a bundle directory → a plan to execute.

    resolve_directory (3)  →  route (4)  →  compile_plan (5)

Composed from the stages, which stay callable on their own: this is the convenience for
a caller that wants the whole path, not a layer that owns anything of its own. Stop
after the resolution for described columns, after the routing for coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.core.schemas import Plan
from src.pipelines.catalog import DEFAULT_BUNDLE, resolve_directory
from src.pipelines.models import Judges, Readers
from src.pipelines.routing import DEFAULT_STANDARD, route_and_compile
from src.router import FieldPlan, ResolvedBundle


@dataclass(frozen=True)
class FieldDrivenRun:
    """Everything one pass produced, for a caller that renders it itself."""

    resolved: ResolvedBundle
    field_plan: FieldPlan
    plan: Plan
    standard: str


def run(
    bundle: Path = DEFAULT_BUNDLE,
    standard: str = DEFAULT_STANDARD,
    *,
    dictionaries: Optional[List[str]] = None,
    documents: Optional[List[str]] = None,
    searched: Optional[List[Path]] = None,
    candidates: int = 5,
    readers: Optional[Readers] = None,
    judges: Optional[Judges] = None,
) -> FieldDrivenRun:
    """Resolve the bundle, route the standard over it, and compile the routing.

    ``dictionaries`` and ``documents`` narrow what the *resolver* reads, by filename;
    ``searched`` narrows which of the resolution's documents the *router* reads, as
    paths. ``readers`` and ``judges`` are the model-backed roles each stage runs with
    (:mod:`src.pipelines.models`); without them the whole path is deterministic.
    """
    resolved = resolve_directory(
        bundle, dictionaries=dictionaries, documents=documents, readers=readers
    )
    field_plan, plan = route_and_compile(
        resolved, standard, candidates=candidates, judges=judges, documents=searched
    )
    return FieldDrivenRun(resolved, field_plan, plan, standard)
