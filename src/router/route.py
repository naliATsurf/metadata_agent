"""Layer 4 — the router: from schema fields to a FieldPlan.

The field-driven inversion. Instead of surveying every source and hoping values
fall out, the router starts from *what it must fill* — the target schema's leaf
fields (:func:`~src.router.schema.walk_schema`) — and routes each to the source
that can answer it. A field falls into one of three buckets:

- **tool** — a property of the data itself (record count), computed by a
  deterministic tool, no search;
- **column** — "which column?", routed over the *enriched* catalog
  (:meth:`Catalog.search`), so opaque names resolved in layer 3 are reachable;
- **document** — a meaning stated only in prose (abstract, licence), routed over
  the document sources.

Each bucket names **where the answer comes from**, so a routing can be read
without recalling a definition. A field nothing answers is ``unanswered``.

The output is a :class:`FieldPlan` — the persisted routing artifact and the
source of truth the M4 compiler turns into executable `Task`s. Coverage falls
straight out of it: a field the router cannot route is flagged **before**
extraction, which is the confabulation signal moved upstream.

Scope (M3): the router builds the artifact by calling the search *methods*
directly. When it later drives execution (M4), those searches fire through the
`search_context` tool under ``attributed_to(Caller(phase="route"))`` so each
becomes provenance-captured evidence; that wiring is deliberately not here yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from src.context.base_context import (
    EvidenceRef,
    Searchable,
    bm25_scores,
    content_terms,
    tokenize,
)
from src.router.catalog import Catalog
from src.router.schema import FieldSpec, walk_schema


@dataclass
class FieldRouting:
    """Where one field is routed, and on what candidate evidence."""

    field_path: str
    query: str                              # the field description, used as the query
    bucket: str                             # tool | column | document | unanswered
    candidates: List[EvidenceRef] = field(default_factory=list)
    assurance: str = "none"                 # high | medium | low | none
    status: str = "routed"                  # routed | unanswered
    # Populated by the M4 compiler, not the router:
    extractor_role: Optional[str] = None
    topology: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_path": self.field_path,
            "query": self.query,
            "bucket": self.bucket,
            "status": self.status,
            "assurance": self.assurance,
            "candidates": [c.to_dict() for c in self.candidates],
            "extractor_role": self.extractor_role,
            "topology": self.topology,
        }


@dataclass
class FieldPlan:
    """The persisted routing artifact: one FieldRouting per leaf field."""

    schema_name: str
    routings: Dict[str, FieldRouting]

    def unanswered(self) -> List[str]:
        """Fields with no candidate source — flagged before extraction runs."""
        return [p for p, r in self.routings.items() if r.status == "unanswered"]

    def coverage(self) -> Dict[str, Any]:
        """A one-glance report: how many fields routed, where, and how grounded."""
        by_bucket: Dict[str, int] = {}
        by_assurance: Dict[str, int] = {}
        for r in self.routings.values():
            by_bucket[r.bucket] = by_bucket.get(r.bucket, 0) + 1
            by_assurance[r.assurance] = by_assurance.get(r.assurance, 0) + 1
        total = len(self.routings)
        unanswered = self.unanswered()
        return {
            "total": total,
            "routed": total - len(unanswered),
            "unanswered": unanswered,
            "by_bucket": by_bucket,
            "by_assurance": by_assurance,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "routings": {p: r.to_dict() for p, r in self.routings.items()},
            "coverage": self.coverage(),
        }


def _search_docs(docs: List[Searchable], query: str, k: int) -> List[EvidenceRef]:
    """Ranked spans across the document sources (scores compare within one corpus)."""
    refs: List[EvidenceRef] = []
    for doc in docs:
        refs.extend(doc.search(query, k=k))
    refs.sort(key=lambda r: r.score, reverse=True)
    return refs[:k]


def _answer_tools():
    """The registered field-answering tools (importing registers them)."""
    import src.tools  # noqa: F401 — ensure the tool registry is populated
    from src.tools.base import field_answering_tools

    return field_answering_tools()


def _structured_candidates(
    query: str, catalog: Optional[Catalog], k: int
) -> List[EvidenceRef]:
    """Rank the query against the *structured* corpus, standard-agnostically.

    The corpus is the field-answering tools (by their own descriptions) plus the
    enriched columns, pooled into one BM25 ranking — both are short, comparable
    documents, so a tool and a column compete on equal footing. The winning
    candidate's ``kind`` then names the bucket: a tool → ``structural`` (bound by
    the tool's declared purpose, not a per-standard keyword), a column →
    ``ambiguous_structural``. No field-name table anywhere.
    """
    entries: List[EvidenceRef] = []
    docs: List[List[str]] = []

    for tool in _answer_tools():
        entries.append(
            EvidenceRef(
                resource="", locator=tool.name, kind="tool",
                snippet=tool.description or tool.name, score=0.0,
            )
        )
        docs.append(tokenize(f"{tool.name} {tool.description or ''}"))

    if catalog is not None:
        for col in catalog.columns:
            entries.append(
                EvidenceRef(
                    resource=col.resource, locator=col.name, kind="computed_column",
                    snippet=f"{col.name}: {col.description or col.value_label or col.dtype}",
                    score=0.0,
                )
            )
            docs.append(tokenize(col.document()))

    scores = bm25_scores(content_terms(query), docs)
    ranked = [
        EvidenceRef(
            resource=e.resource, locator=e.locator, kind=e.kind,
            snippet=e.snippet, score=s,
        )
        for e, s in zip(entries, scores)
        if s > 0
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:k]


def _route_one(
    spec: FieldSpec, catalog: Optional[Catalog], docs: List[Searchable], k: int
) -> FieldRouting:
    query = spec.description or spec.path

    # Structured corpus first (tools + columns, one ranking). Its winner's kind
    # names the bucket. A field whose meaning matches nothing structured — a
    # narrative field — produces no hit here and falls through to the documents.
    structured = _structured_candidates(query, catalog, k)
    if structured:
        top = structured[0]
        bucket = "tool" if top.kind == "tool" else "column"
        return FieldRouting(
            field_path=spec.path, query=query, bucket=bucket,
            candidates=structured, assurance=_assurance(bucket, top, catalog),
        )

    doc_hits = _search_docs(docs, query, k)
    if doc_hits:
        return FieldRouting(
            field_path=spec.path, query=query, bucket="document",
            candidates=doc_hits, assurance="low",
        )

    return FieldRouting(
        field_path=spec.path, query=query, bucket="unanswered",
        candidates=[], assurance="none", status="unanswered",
    )


def _assurance(bucket: str, top: EvidenceRef, catalog: Optional[Catalog]) -> str:
    """Grade the routing from the top candidate.

    Two-hop for a computed column: the computation is recomputable (high), but the
    *interpretation* — that this column means what the field asks — is only as
    strong as the catalog resolution behind it, so the weaker hop wins. A retrieved
    span is quoted-only (low) until a verifier confirms it (a later milestone).
    """
    if bucket == "column" and catalog is not None:
        # Disambiguate by resource: a multi-table catalog can hold the same column
        # name in two tables, and the routed candidate names the one that won.
        column = catalog.find(top.locator, top.resource)
        return column.link_confidence if column else "low"
    if bucket == "document":
        return "low"
    return "high"


def route_fields(
    schema: Type[BaseModel],
    catalog: Optional[Catalog] = None,
    docs: Optional[List[Searchable]] = None,
    k: int = 3,
) -> FieldPlan:
    """Route every leaf field of ``schema`` to a source, producing a FieldPlan.

    ``catalog`` is the enriched column catalog (layer 3) for structural fields;
    ``docs`` are the document sources for narrative fields. A field that neither
    can answer is left ``unresolved`` — coverage, computed before any extraction.
    """
    docs = docs or []
    routings = {
        spec.path: _route_one(spec, catalog, docs, k)
        for spec in walk_schema(schema)
    }
    return FieldPlan(schema_name=schema.__name__, routings=routings)
