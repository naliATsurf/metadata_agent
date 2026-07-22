"""Field-driven routing (see docs/development/plan_field_router.md).

Fills a metadata standard field by field: flatten the target schema to leaf
fields (:mod:`~src.router.schema`), route each to the source that can answer it,
then extract from those candidates only. This package holds the modality-agnostic
router machinery; the sources it routes over are :class:`~src.context.Searchable`
contexts.
"""

from src.router.catalog import Catalog, ResolvedColumn, resolve_catalog
from src.router.schema import FieldSpec, walk_schema

__all__ = [
    "Catalog",
    "FieldSpec",
    "ResolvedColumn",
    "resolve_catalog",
    "walk_schema",
]
