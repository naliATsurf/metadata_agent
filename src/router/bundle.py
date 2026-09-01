"""Classifying a directory of files into the roles the resolver plays them in.

A bundle arrives as a folder. Something has to decide which files are data, which
describe that data, and which are prose — and doing it by filename convention makes
every caller restate what the resolver can already work out. A document identifies
itself by extension, and a codebook by its shape (a column whose values are the
bundle's column names), so classification needs nothing declared about a bundle.

That is what production wants. :func:`select` is the other half, for a caller that
wants to resolve a *subset* — a UI comparing inputs, or a run isolating one source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.context import create_context
from src.router.catalog import looks_like_dictionary


#: Passed to :func:`select` to use none of a source kind. A filename could never be
#: this, and it gives a command line a way to say "none" that an empty list cannot.
NONE = "none"

_DOC_SUFFIXES = ("*.md", "*.txt")


@dataclass(frozen=True)
class Bundle:
    """A directory's files, sorted into the roles they will be used in."""

    root: Path
    tables: List[Path]
    codebooks: List[Path]
    documents: List[Path]

    def summary(self) -> str:
        """A one-line description of what was found."""
        return (
            f"{len(self.tables)} tables, {len(self.codebooks)} codebooks, "
            f"{len(self.documents)} documents"
        )


def discover_bundle(root: Path) -> Bundle:
    """Classify every file in ``root`` — no filenames, no conventions.

    :param root: The bundle directory.
    :returns: The classified :class:`Bundle`.
    :raises ValueError: If the directory holds no CSV, or holds nothing but codebooks.
    """
    csvs = sorted(root.glob("*.csv"))
    documents = sorted(p for pattern in _DOC_SUFFIXES for p in root.glob(pattern))
    if not csvs:
        raise ValueError(f"No CSV found in {root}.")

    contexts = {path: create_context(str(path), name=path.stem) for path in csvs}
    vocabulary = [
        name
        for context in contexts.values()
        for name in context.get_resource_info(context.resources[0]).field_names
    ]
    codebooks = [p for p in csvs if looks_like_dictionary(contexts[p], vocabulary)]
    tables = [p for p in csvs if p not in codebooks]
    if not tables:
        raise ValueError(f"No data table in {root}; every CSV looks like a codebook.")
    return Bundle(root=root, tables=tables, codebooks=codebooks, documents=documents)


def select(discovered: List[Path], chosen: Optional[List[str]]) -> List[Path]:
    """Narrow what :func:`discover_bundle` found to a chosen subset.

    Three states, so a UI can express every one of them and the equivalent command
    line round-trips:

    * ``None`` — nothing was chosen: use everything discovered (production).
    * ``[NONE]`` — use nothing of this kind.
    * filenames — use exactly those.

    :param discovered: What classification found.
    :param chosen: The caller's choice, in one of the three forms above.
    :returns: The paths to use.
    """
    if chosen is None:
        return discovered
    names = {c.strip() for c in chosen}
    if NONE in {n.lower() for n in names}:
        return []
    return [p for p in discovered if p.name in names]
