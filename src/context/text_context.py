"""
Text ExecutionContext Implementation.

Represents a corpus of free-text documents (plain text, Markdown). Unlike the
tabular contexts, data access is document-oriented: whole text, chunk
iteration, and search — there is no DataFrame contract to satisfy.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from src.context.base_context import (
    ContextType,
    ExecutionContext,
    TextResourceInfo,
)


@dataclass
class TextChunk:
    """A contiguous span of a text resource."""

    resource: str
    index: int
    text: str
    start_offset: int

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "index": self.index,
            "text": self.text,
            "start_offset": self.start_offset,
            "char_count": self.char_count,
        }


def paragraph_chunker(text: str) -> List[int]:
    """Return chunk start offsets, splitting on blank lines."""
    offsets = [0]
    for match in re.finditer(r"\n\s*\n", text):
        if match.end() < len(text):
            offsets.append(match.end())
    return offsets


def fixed_size_chunker(chunk_size: int) -> Callable[[str], List[int]]:
    """Return a chunker splitting every ``chunk_size`` characters."""

    def _chunker(text: str) -> List[int]:
        return list(range(0, len(text), chunk_size)) or [0]

    return _chunker


class TextContext(ExecutionContext):
    """
    ExecutionContext implementation for free-text files.

    Each input file is one resource. Chunking is a read-time concern
    (``iter_chunks``), not a storage format: the default splits on blank
    lines (paragraphs); pass a different ``chunker`` to override.
    """

    PREVIEW_CHARS = 500

    def __init__(
        self,
        source: Union[str, List[str], Dict[str, str]],
        name: str = "text_context",
        description: Optional[str] = None,
        encoding: str = "utf-8",
        chunker: Optional[Callable[[str], List[int]]] = None,
    ):
        super().__init__(name=name, description=description)

        self._resources: Dict[str, str] = self._normalize_source(source)
        self._encoding = encoding
        self._chunker = chunker or paragraph_chunker
        self._text_cache: Dict[str, str] = {}

    def _normalize_source(
        self, source: Union[str, List[str], Dict[str, str]]
    ) -> Dict[str, str]:
        """Convert various input formats to dict of resource_name -> path."""
        if isinstance(source, str):
            return {Path(source).stem: os.path.abspath(source)}

        elif isinstance(source, list):
            return {Path(p).stem: os.path.abspath(p) for p in source}

        elif isinstance(source, dict):
            return {
                name: os.path.abspath(path) for name, path in source.items()
            }

        else:
            raise ValueError(f"Invalid source type: {type(source)}")

    def _check_resource(self, resource: str) -> str:
        if resource not in self._resources:
            raise ValueError(
                f"Resource '{resource}' not found. Available: {self.resources}"
            )
        return self._resources[resource]

    @property
    def context_type(self) -> ContextType:
        return ContextType.TEXT

    @property
    def resources(self) -> List[str]:
        return list(self._resources.keys())

    def read_text(self, resource: str, limit: Optional[int] = None) -> str:
        """
        Read the full text of a resource.

        Args:
            resource: Resource name.
            limit: If given, return at most this many characters.
        """
        file_path = self._check_resource(resource)

        if resource not in self._text_cache:
            with open(file_path, "r", encoding=self._encoding) as f:
                self._text_cache[resource] = f.read()

        text = self._text_cache[resource]
        return text[:limit] if limit else text

    def iter_chunks(
        self,
        resource: str,
        chunker: Optional[Callable[[str], List[int]]] = None,
    ) -> Iterator[TextChunk]:
        """
        Iterate over a resource as chunks.

        The chunker maps the full text to a list of chunk start offsets;
        defaults to the context-level chunker (paragraphs).
        """
        text = self.read_text(resource)
        offsets = (chunker or self._chunker)(text)

        for i, start in enumerate(offsets):
            end = offsets[i + 1] if i + 1 < len(offsets) else len(text)
            chunk_text = text[start:end].strip()
            if chunk_text:
                yield TextChunk(
                    resource=resource, index=i, text=chunk_text, start_offset=start
                )

    def get_chunks(self, resource: str) -> List[TextChunk]:
        """Return all chunks of a resource as a list."""
        return list(self.iter_chunks(resource))

    def search(
        self,
        query: str,
        resource: Optional[str] = None,
        regex: bool = False,
        context_chars: int = 80,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search for a query string (or regex) across one or all resources.

        Returns matches with surrounding context, ordered by position.
        """
        pattern = re.compile(
            query if regex else re.escape(query), re.IGNORECASE
        )
        targets = [resource] if resource else self.resources

        results = []
        for res in targets:
            text = self.read_text(res)
            for match in pattern.finditer(text):
                start = max(0, match.start() - context_chars)
                end = min(len(text), match.end() + context_chars)
                results.append(
                    {
                        "resource": res,
                        "offset": match.start(),
                        "match": match.group(),
                        "context": text[start:end],
                    }
                )
                if len(results) >= max_results:
                    return results
        return results

    def _load_resource_info(self, resource: str) -> TextResourceInfo:
        """Load metadata for a text file."""
        file_path = self._check_resource(resource)
        text = self.read_text(resource)
        chunks = self.get_chunks(resource)

        preview = text[: self.PREVIEW_CHARS].strip()

        return TextResourceInfo(
            name=resource,
            item_count=len(chunks),
            location=file_path,
            size_in_bytes=os.path.getsize(file_path) if os.path.exists(file_path) else None,
            description=f"Text document. Preview: {preview}" if preview else "Empty text document.",
            char_count=len(text),
            word_count=len(text.split()),
            line_count=text.count("\n") + 1 if text else 0,
            encoding=self._encoding,
        )

    def get_file_path(self, resource: str) -> str:
        return self._check_resource(resource)

    def get_all_file_paths(self) -> Dict[str, str]:
        return self._resources.copy()

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["file_paths"] = self._resources
        return base
