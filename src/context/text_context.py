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
    EvidenceRef,
    Searchable,
    TextResourceInfo,
    bm25_scores,
    content_terms,
    tokenize,
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


_HEADING_RE = re.compile(r"(?m)^#{1,6}[ \t]")


def markdown_chunker(text: str) -> List[int]:
    """Return chunk start offsets, splitting *before* each Markdown heading.

    Each chunk is a section — its heading line followed by its body — so the
    heading travels with the content it introduces. Because a chunk literally
    begins with its heading, the heading's terms are part of what ``search``
    ranks (a section under ``## Licence`` scores for the ``license`` field) with
    no separate context field and no break to the span/offset contract.
    """
    offsets = [0]
    for match in _HEADING_RE.finditer(text):
        if match.start() != 0:
            offsets.append(match.start())
    return sorted(set(offsets))


def _default_chunker_for(text: str) -> Callable[[str], List[int]]:
    """Pick a chunker from the content: heading-aware for Markdown, else paragraphs."""
    return markdown_chunker if _HEADING_RE.search(text) else paragraph_chunker


class TextContext(Searchable):
    """
    ExecutionContext implementation for free-text files.

    Each input file is one resource. Chunking is a read-time concern
    (``iter_chunks``), not a storage format: the default splits on blank
    lines (paragraphs); pass a different ``chunker`` to override.

    As a :class:`Searchable`, it ranks its chunks against a query
    (:meth:`search`, returning :class:`EvidenceRef` spans). :meth:`grep` is the
    separate literal/regex match utility — exact, unranked — kept distinct from
    the fuzzy capability search.
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
        # None means "choose per resource from its content" (see iter_chunks);
        # an explicit chunker overrides that everywhere.
        self._chunker = chunker
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

        The chunker maps the full text to a list of chunk start offsets. An
        explicit ``chunker`` (here or on the context) wins; otherwise one is
        chosen from the content — heading-aware for Markdown, paragraphs
        otherwise (see :func:`_default_chunker_for`).
        """
        text = self.read_text(resource)
        selected = chunker or self._chunker or _default_chunker_for(text)
        offsets = selected(text)

        for i, start in enumerate(offsets):
            end = offsets[i + 1] if i + 1 < len(offsets) else len(text)
            raw = text[start:end]
            chunk_text = raw.strip()
            if chunk_text:
                # start_offset points at the first non-whitespace char, so
                # text[start_offset : start_offset + len(chunk_text)] == chunk_text
                # exactly — the span/offset contract the provenance verifier relies on.
                lead = len(raw) - len(raw.lstrip())
                yield TextChunk(
                    resource=resource, index=i, text=chunk_text, start_offset=start + lead
                )

    def get_chunks(self, resource: str) -> List[TextChunk]:
        """Return all chunks of a resource as a list."""
        return list(self.iter_chunks(resource))

    def preview(self, resource: str, n: int = 5) -> str:
        """Render the first ``n`` chunks as text."""
        return "\n\n".join(chunk.text for chunk in self.get_chunks(resource)[:n])

    def search(self, query: str, resource: str = "", k: int = 5) -> List[EvidenceRef]:
        """Rank chunks against the query and return the top ``k`` as spans.

        The :class:`Searchable` capability: a fuzzy, ranked locate that may
        return nothing. Chunks are scored by lexical overlap with the query (the
        dependency-free baseline; an embedding scorer replaces it later) and
        returned as ``quoted_span`` refs — pointers an extractor then quotes.
        For exact substring/regex matching, use :meth:`grep` instead.
        """
        query_terms = content_terms(query)
        targets = [resource] if resource else self.resources

        chunks = [chunk for res in targets for chunk in self.iter_chunks(res)]
        scores = bm25_scores(query_terms, [tokenize(c.text) for c in chunks])

        refs: List[EvidenceRef] = []
        for chunk, score in zip(chunks, scores):
            if score > 0:
                snippet = chunk.text[:200] + ("…" if len(chunk.text) > 200 else "")
                refs.append(
                    EvidenceRef(
                        resource=chunk.resource,
                        locator=(chunk.start_offset, chunk.start_offset + len(chunk.text)),
                        kind="quoted_span",
                        snippet=snippet,
                        score=score,
                    )
                )
        refs.sort(key=lambda r: r.score, reverse=True)
        return refs[:k]

    def grep(
        self,
        query: str,
        resource: Optional[str] = None,
        regex: bool = False,
        context_chars: int = 80,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Literal (or regex) match of a query across one or all resources.

        Exact and unranked — distinct from the fuzzy :meth:`search` capability.
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
