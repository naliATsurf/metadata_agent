"""Stage: a resolved catalog → a field plan, and a field plan → an executable plan.

Two steps, separately runnable, because they answer different questions and are varied
separately: routing decides *where* each schema field is answered (layer 4), compiling
lays that out as the tasks an executor runs (layer 5). A caller that only wants coverage
— which fields this bundle cannot answer — stops after :func:`route`.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from src.context import create_context
from src.core.schemas import Plan
from src.pipelines.models import Judges
from src.router import FieldPlan, ResolvedBundle, compile_field_plan, route_fields
from src.standards import get_schema_for_standard

#: The standard routed when none is named.
DEFAULT_STANDARD = "sharetrait_basic_no_trait"


def route(
    resolved: ResolvedBundle,
    standard: str = DEFAULT_STANDARD,
    *,
    candidates: int = 5,
    judges: Optional[Judges] = None,
    documents: Optional[List[Path]] = None,
) -> FieldPlan:
    """Route every field of ``standard`` over a resolution (layer 4).

    ``documents`` narrows which of the resolution's documents are *searched*, without
    resolving again. The two document choices answer different questions: the resolver's
    picks what described the columns, this picks what is read to answer the fields no
    column answers. A bundle carrying three variants of one README routes them as three
    rival sources unless one is named here.
    """
    schema = get_schema_for_standard(standard)
    if schema is None:
        raise ValueError(f"Unknown standard {standard!r}.")
    judges = judges or Judges()
    searched = resolved.documents if documents is None else documents
    return route_fields(
        schema,
        catalog=resolved.catalog,
        docs=[create_context(str(p), name=p.stem) for p in searched],
        k=candidates,
        tool_matcher=judges.tools,
        matcher=judges.columns,
        reader=judges.passages,
    )


def compile_plan(field_plan: FieldPlan, **options) -> Plan:
    """Lay a field plan out as the tasks an executor runs (layer 5)."""
    return compile_field_plan(field_plan, **options)


def route_and_compile(
    resolved: ResolvedBundle,
    standard: str = DEFAULT_STANDARD,
    *,
    candidates: int = 5,
    judges: Optional[Judges] = None,
    documents: Optional[List[Path]] = None,
) -> Tuple[FieldPlan, Plan]:
    """Both steps, for a caller that wants the executable plan."""
    field_plan = route(
        resolved, standard, candidates=candidates, judges=judges, documents=documents
    )
    return field_plan, compile_plan(field_plan)
