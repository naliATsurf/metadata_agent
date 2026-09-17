"""Field-driven routing (see docs/development/plans/field-router.md).

Fills a metadata standard field by field: flatten the target schema to leaf
fields (:mod:`~src.router.schema`), route each to the source that can answer it,
then extract from those candidates only. This package holds the modality-agnostic
router machinery; the sources it routes over are :class:`~src.context.Searchable`
contexts.

The layers, in the order a field passes through them:

=================================  ======================================================
:mod:`~src.router.schema`          flatten the target schema to leaf fields
:mod:`~src.router.catalog`         resolve each column's meaning from the bundle (3)
:mod:`~src.router.route`           rank sources per field, lexically (4)
:mod:`~src.router.type_fit`        grade whether type and units fit the field (4a)    
:mod:`~src.router.column_matcher`  match fields to columns and tools (4b)
:mod:`~src.router.passage_reader`  read passages for the fields they state (4b)
:mod:`~src.router.compile`         lay the routing out as executable tasks (5)
=================================  ======================================================

Layers 4a and 4b exist because ranking alone over-answers: BM25's only reject rule
is a non-empty score. 4a is deterministic but blunt, so it only lowers confidence and
never removes a candidate; 4b is a model and may be wrong, so its every answer is
refereed by code.
"""

from src.router.catalog import (
    CachedProseReader,
    Catalog,
    Claim,
    ClaimComparer,
    LLMClaimComparer,
    LLMProseReader,
    ProseReader,
    ReadResult,
    ResolvedColumn,
    looks_like_dictionary,
    resolve_bundle,
    resolve_catalog,
)
from src.router.bundle import NONE, Bundle, ResolvedBundle, discover_bundle, select
from src.router.compile import compile_field_plan
from src.router.display import (
    METHOD_LABELS,
    catalog_conflicts,
    catalog_overview,
    catalog_summary,
    render_catalog,
)
from src.router.column_matcher import (
    ColumnGroup,
    ColumnMatcher,
    LLMColumnMatcher,
    merge_columns,
)
from src.router.judge import Verdict, candidate_ref
from src.router.passage_reader import LLMPassageReader, PassageReader
from src.router.route import FieldPlan, FieldRouting, route_fields
from src.router.type_fit import mismatch, mismatches
from src.router.schema import FieldSpec, walk_schema

__all__ = [
    "METHOD_LABELS",
    "NONE",
    "Bundle",
    "ResolvedBundle",
    "discover_bundle",
    "select",
    "CachedProseReader",
    "Catalog",
    "FieldPlan",
    "FieldRouting",
    "ColumnGroup",
    "ColumnMatcher",
    "FieldSpec",
    "LLMColumnMatcher",
    "LLMPassageReader",
    "PassageReader",
    "merge_columns",
    "Claim",
    "ClaimComparer",
    "LLMClaimComparer",
    "LLMProseReader",
    "ProseReader",
    "ReadResult",
    "ResolvedColumn",
    "Verdict",
    "candidate_ref",
    "catalog_conflicts",
    "catalog_overview",
    "catalog_summary",
    "looks_like_dictionary",
    "compile_field_plan",
    "render_catalog",
    "resolve_bundle",
    "resolve_catalog",
    "route_fields",
    "mismatch",
    "mismatches",
    "walk_schema",
]
