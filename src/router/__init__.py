"""Field-driven routing (see docs/development/plan_field_router.md).

Fills a metadata standard field by field: flatten the target schema to leaf
fields (:mod:`~src.router.schema`), route each to the source that can answer it,
then extract from those candidates only. This package holds the modality-agnostic
router machinery; the sources it routes over are :class:`~src.context.Searchable`
contexts.
"""

from src.router.catalog import (
    Catalog,
    DeterministicProseReader,
    LLMProseReader,
    ProseReader,
    ReadResult,
    ResolvedColumn,
    resolve_bundle,
    resolve_catalog,
)
from src.router.compile import compile_field_plan
from src.router.route import FieldPlan, FieldRouting, route_fields
from src.router.schema import FieldSpec, walk_schema

__all__ = [
    "Catalog",
    "DeterministicProseReader",
    "FieldPlan",
    "FieldRouting",
    "FieldSpec",
    "LLMProseReader",
    "ProseReader",
    "ReadResult",
    "ResolvedColumn",
    "compile_field_plan",
    "resolve_bundle",
    "resolve_catalog",
    "route_fields",
    "walk_schema",
]
