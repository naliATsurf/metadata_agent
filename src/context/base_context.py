"""
ExecutionContext Base Classes and Models.

This module defines the abstract base class for all execution contexts and
common data models used across the system.

The ExecutionContext abstraction provides a unified interface for the "world"
in which the agents operate. This can be:
- A structured data source (database, CSVs)
- A free-text corpus (plain text, Markdown)
- A codebase (file system)
- An API
- A website
- etc.

The class hierarchy separates the universal contract from modality-specific
data access:

- ``ExecutionContext``: what every source can answer — which resources exist,
  their basic properties (``ResourceInfo``), and how they relate.
- ``TabularContext``: adds row/column access (``read_resource`` returning a
  ``pd.DataFrame``). Implemented by CSV and SQLite contexts.
- ``TextContext`` (see ``text_context.py``): adds document access
  (``read_text``, ``iter_chunks``, ``search``).

Tools that need a specific access style gate on the subclass
(``isinstance(ctx, TabularContext)``) rather than assuming every context is
tabular.
"""

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Dict, Iterator, List, Optional, Union

import pandas as pd


class ContextType(str, Enum):
    """Enumeration of supported execution context types."""

    SINGLE_CSV = "single_csv"
    MULTI_CSV = "multi_csv"
    TEXT = "text"
    SQLITE = "sqlite"
    UNKNOWN = "unknown"


@dataclass
class FieldInfo:
    """Information about a single field within a resource."""

    name: str
    dtype: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_key_reference: Optional[str] = None  # "resource.field" format
    description: Optional[str] = None
    sample_values: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "nullable": self.nullable,
            "is_primary_key": self.is_primary_key,
            "is_foreign_key": self.is_foreign_key,
            "foreign_key_reference": self.foreign_key_reference,
            "description": self.description,
            "sample_values": self.sample_values[:5],
        }


@dataclass
class ResourceInfo:
    """
    Modality-agnostic information about a single resource.

    Holds only what every source can report. Modality-specific metadata lives
    on subclasses (:class:`TabularResourceInfo`, :class:`TextResourceInfo`),
    each of which extends :meth:`to_dict` and :meth:`summary` — so consumers
    (schema serialization, the orchestrator's context summary) render the
    right vocabulary without branching on context type.
    """

    kind: ClassVar[str] = "resource"

    name: str
    item_count: Optional[int] = None  # rows, chunks, records, ...
    location: Optional[str] = None  # for file-based or URL-based resources
    size_in_bytes: Optional[int] = None
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "item_count": self.item_count,
            "location": self.location,
            "size_in_bytes": self.size_in_bytes,
            "description": self.description,
        }

    def summary(self) -> str:
        """One-line human/LLM-facing description of this resource."""
        return f"{self.item_count} items" if self.item_count is not None else "resource"


@dataclass
class TabularResourceInfo(ResourceInfo):
    """Resource metadata for row/column sources (CSV, SQLite)."""

    kind: ClassVar[str] = "tabular"

    fields: List[FieldInfo] = field(default_factory=list)
    primary_key: Optional[Union[str, List[str]]] = None

    @property
    def field_names(self) -> List[str]:
        return [f.name for f in self.fields]

    @property
    def field_count(self) -> int:
        return len(self.fields)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "field_count": self.field_count,
                "fields": [f.to_dict() for f in self.fields],
                "primary_key": self.primary_key,
            }
        )
        return data

    def summary(self) -> str:
        preview = ", ".join(self.field_names[:5])
        if len(self.field_names) > 5:
            preview += "..."
        return f"{self.item_count} rows, {self.field_count} fields ({preview})"


@dataclass
class TextResourceInfo(ResourceInfo):
    """Resource metadata for free-text documents (plain text, Markdown)."""

    kind: ClassVar[str] = "text"

    char_count: Optional[int] = None
    word_count: Optional[int] = None
    line_count: Optional[int] = None
    language: Optional[str] = None
    encoding: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "char_count": self.char_count,
                "word_count": self.word_count,
                "line_count": self.line_count,
                "language": self.language,
                "encoding": self.encoding,
            }
        )
        return data

    def summary(self) -> str:
        parts = [f"{self.item_count} chunks"]
        if self.word_count is not None:
            parts.append(f"{self.word_count} words")
        if self.language:
            parts.append(f"lang={self.language}")
        return ", ".join(parts)


@dataclass
class RelationshipInfo:
    """
    Information about a relationship between resources.

    Relationships are resource-level by default. ``from_field``/``to_field``
    are the tabular refinement: for CSV/SQLite they name the joined columns
    (foreign keys), but they stay ``None`` for relationships that hold between
    whole resources — e.g. one document citing another, or two documents
    sharing an entity. ``relationship_type`` is a free string, so besides the
    tabular cardinalities ("one-to-one", "one-to-many", "many-to-many") it can
    carry values like "cites" or "shared-entity".
    """

    from_resource: str
    to_resource: str
    relationship_type: str
    from_field: Optional[str] = None
    to_field: Optional[str] = None
    confidence: float = 0.0
    is_verified: bool = False
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_resource": self.from_resource,
            "from_field": self.from_field,
            "to_resource": self.to_resource,
            "to_field": self.to_field,
            "relationship_type": self.relationship_type,
            "confidence": self.confidence,
            "is_verified": self.is_verified,
            "description": self.description,
        }

    def describe(self) -> str:
        """One-line rendering that degrades gracefully when fields are absent."""
        if self.from_field and self.to_field:
            endpoints = (
                f"{self.from_resource}.{self.from_field} -> "
                f"{self.to_resource}.{self.to_field}"
            )
        else:
            endpoints = f"{self.from_resource} -> {self.to_resource}"
        return f"{endpoints} ({self.relationship_type})"


class ExecutionContext(ABC):
    """
    Abstract base class for all execution contexts.

    Holds only the modality-agnostic contract: resource inventory, resource
    metadata, schema, and relationships. Data access methods live on
    modality-specific subclasses such as :class:`TabularContext`.
    """

    def __init__(self, name: str = "context", description: Optional[str] = None):
        self._name = name
        self._description = description
        self._resource_cache: Dict[str, ResourceInfo] = {}
        self._relationship_cache: Optional[List[RelationshipInfo]] = None

    @property
    @abstractmethod
    def context_type(self) -> ContextType:
        """Return the type of this context."""
        pass

    @property
    @abstractmethod
    def resources(self) -> List[str]:
        """Return list of resource names in this context."""
        pass

    @abstractmethod
    def _load_resource_info(self, resource: str) -> ResourceInfo:
        """Load metadata for a specific resource."""
        pass

    @abstractmethod
    def preview(self, resource: str, n: int = 5) -> str:
        """Render the first ``n`` items of a resource as readable text.

        Universal question, per-modality answer: rows for a table, chunks for a
        document. Lets sampling be a modality-agnostic tool rather than one
        that branches on context type.
        """
        pass

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> Optional[str]:
        return self._description

    @property
    def is_multi_resource(self) -> bool:
        """True when the context holds more than one resource.

        Modality-agnostic: true for multi-CSV, multi-table, and multi-document
        corpora alike. Drives whether pipeline steps target resources
        individually or operate context-wide.
        """
        return len(self.resources) > 1

    def get_resource_info(self, resource: str) -> ResourceInfo:
        if resource not in self.resources:
            raise ValueError(
                f"Resource '{resource}' not found. Available: {self.resources}"
            )

        if resource not in self._resource_cache:
            self._resource_cache[resource] = self._load_resource_info(resource)

        return self._resource_cache[resource]

    def get_all_resource_info(self) -> Dict[str, ResourceInfo]:
        return {
            resource: self.get_resource_info(resource) for resource in self.resources
        }

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "context_type": self.context_type.value,
            "is_multi_resource": self.is_multi_resource,
            "resources": {
                name: info.to_dict()
                for name, info in self.get_all_resource_info().items()
            },
            "relationships": [r.to_dict() for r in self.get_relationships()],
        }

    def get_relationships(self) -> List[RelationshipInfo]:
        if self._relationship_cache is None:
            self._relationship_cache = self._discover_relationships()
        return self._relationship_cache

    def _discover_relationships(self) -> List[RelationshipInfo]:
        return []

    def validate(self) -> bool:
        if not self.resources:
            raise ValueError("Context has no resources")

        for resource in self.resources:
            self.get_resource_info(resource)

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "context_type": self.context_type.value,
            "resources": self.resources,
            "is_multi_resource": self.is_multi_resource,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name='{self.name}', "
            f"resources={self.resources}, "
            f"context_type='{self.context_type.value}')"
        )

    def __str__(self) -> str:
        resource_info = (
            f"{len(self.resources)} resource(s)"
            if len(self.resources) != 1
            else self.resources[0]
        )
        return f"{self.name} ({self.context_type.value}: {resource_info})"


# ---------------------------------------------------------------------------
# Searchable — the capability the field-driven router routes over
#
# See docs/development/plans/field-router.md. Search returns *pointers*, not
# values: a column a computation then runs on (tabular), or a span an extractor
# then quotes (text). Ranking is BM25 over the candidate documents (columns or
# chunks) — a strong, deterministic, dependency-free lexical baseline. It is the
# seam a dense/embedding scorer replaces for the narrative fields (an open
# decision in the plan); staying lexical keeps the results replayable, which the
# assurance grade depends on.
# ---------------------------------------------------------------------------


@dataclass
class EvidenceRef:
    """A candidate location that could answer a field's query.

    ``kind`` carries the assurance distinction — ``computed_column`` >
    ``verified_span`` > ``quoted_span`` — and ``locator`` is a column name
    (tabular) or a ``(start, end)`` char span (text).
    """

    resource: str
    locator: Any
    kind: str
    snippet: str
    score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "locator": self.locator,
            "kind": self.kind,
            "snippet": self.snippet,
            "score": round(self.score, 4),
        }


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small English stop list. It is the noise gate on the query side: a field
# description is a sentence ("How the data was collected"), and without this a
# chunk matching only "the"/"for" would surface. Kept deliberately small; the
# real discrimination comes from BM25's idf.
_STOPWORDS = frozenset(
    """a an and are as at be been by for from had has have in into is it its of on
    or over per that the their then there these this those to under was were what
    when where which who will with within you your our we they he she""".split()
)


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens."""
    return _TOKEN_RE.findall((text or "").lower())


def content_terms(query: str) -> List[str]:
    """Query tokens with stop words removed — the terms worth matching on."""
    return [t for t in tokenize(query) if t not in _STOPWORDS]


def bm25_scores(
    query_terms: List[str],
    docs: List[List[str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """Okapi BM25 score of each tokenized document against the query.

    Rewards rare, discriminative terms (idf) and term frequency, normalized for
    document length. Deterministic and dependency-free. A document sharing no
    query term scores 0 — which is how an opaque column (``la``) against a
    semantic query (``latitude``) abstains, the semantic gap catalog resolution
    (layer 3) closes. Returned scores are aligned with ``docs``.
    """
    n = len(docs)
    query_set = set(query_terms)
    if n == 0 or not query_set:
        return [0.0] * n

    counters = [Counter(d) for d in docs]
    lengths = [len(d) for d in docs]
    avgdl = sum(lengths) / n or 1.0

    df: Dict[str, int] = {}
    for counter in counters:
        for term in counter:
            df[term] = df.get(term, 0) + 1

    scores: List[float] = []
    for counter, length in zip(counters, lengths):
        score = 0.0
        for term in query_set:
            freq = counter.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (freq * (k1 + 1)) / (
                freq + k1 * (1 - b + b * length / avgdl)
            )
        scores.append(score)
    return scores


class Searchable(ExecutionContext):
    """Capability: locate candidate evidence for a query.

    A context is ``Searchable`` when it can rank its own contents against a
    field's information need and return :class:`EvidenceRef` pointers. Each
    modality implements it differently — a table matches the query against its
    column catalog, a document against its text — but tools gate on the one
    capability (``requires=Searchable``), so the router routes over any modality
    without branching. Ranked and fuzzy by design: ``search`` may return
    nothing, which is the signal that a field has no source here.
    """

    @abstractmethod
    def search(self, query: str, resource: str = "", k: int = 5) -> List[EvidenceRef]:
        """Return up to ``k`` candidate refs for ``query``, best score first."""
        pass


class TabularContext(Searchable):
    """
    Abstract base class for row/column-oriented execution contexts.

    Adds DataFrame-based access on top of the universal contract. CSV and
    SQLite contexts implement this; tools that operate on columns (statistics,
    missing values, temporal/spatial column detection) require it. As a
    :class:`Searchable`, it also ranks its columns against a query
    (:meth:`search`).
    """

    @abstractmethod
    def read_resource(
        self,
        resource: str,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        **kwargs,
    ) -> pd.DataFrame:
        """Read a resource into a pandas DataFrame."""
        pass

    @abstractmethod
    def iter_resource(
        self, resource: str, chunksize: int = 10000, **kwargs
    ) -> Iterator[pd.DataFrame]:
        """Iterate over a resource in chunks."""
        pass

    def get_field_values(
        self, resource: str, field: str, limit: Optional[int] = None
    ) -> List[Any]:
        df = self.read_resource(resource, fields=[field])
        values = df[field].dropna().unique().tolist()
        if limit:
            return values[:limit]
        return values

    def preview(self, resource: str, n: int = 5) -> str:
        return self.read_resource(resource, limit=n).to_string()

    def search(self, query: str, resource: str = "", k: int = 5) -> List[EvidenceRef]:
        """Rank columns against the query over the identifier catalog.

        Generalises ``detect_spatial_columns`` / ``detect_temporal_columns`` from
        two hardcoded field types to any query: score each column's name (plus
        any description and sample values) against the query terms and return the
        top matches as ``computed_column`` refs — pointers a computation tool then
        runs on. Opaque names score low here by construction; catalog resolution
        (layer 3) enriches the catalog before this runs.
        """
        query_terms = content_terms(query)
        targets = [resource] if resource else self.resources

        # Each column is a document: its name plus any description and samples.
        columns = []  # (resource, FieldInfo, doc_terms)
        for res in targets:
            info = self.get_resource_info(res)
            for f in getattr(info, "fields", []):
                doc_terms = tokenize(f.name)
                if f.description:
                    doc_terms += tokenize(f.description)
                doc_terms += tokenize(" ".join(str(v) for v in f.sample_values[:5]))
                columns.append((res, f, doc_terms))

        scores = bm25_scores(query_terms, [doc for _, _, doc in columns])
        refs: List[EvidenceRef] = []
        for (res, f, _), score in zip(columns, scores):
            if score > 0:
                samples = ", ".join(str(v) for v in f.sample_values[:3])
                refs.append(
                    EvidenceRef(
                        resource=res,
                        locator=f.name,
                        kind="computed_column",
                        snippet=f"{f.name} ({f.dtype})"
                        + (f" e.g. {samples}" if samples else ""),
                        score=score,
                    )
                )
        refs.sort(key=lambda r: r.score, reverse=True)
        return refs[:k]
