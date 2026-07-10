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

from abc import ABC, abstractmethod
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


class TabularContext(ExecutionContext):
    """
    Abstract base class for row/column-oriented execution contexts.

    Adds DataFrame-based access on top of the universal contract. CSV and
    SQLite contexts implement this; tools that operate on columns (statistics,
    missing values, temporal/spatial column detection) require it.
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
