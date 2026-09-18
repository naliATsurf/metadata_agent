"""Stage: a bundle's files → a resolved catalog (layer 3).

Runnable on its own. A caller that only wants described columns — to inspect them, to
save a resolution, to route it later, or to feed something else entirely — needs this
stage and nothing after it.

It composes; it decides nothing. Discovery, selection and resolution each live in
:mod:`src.router`, and what this adds is the order they run in and the resolution
artifact they produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from src.context import create_context
from src.pipelines.models import Readers
from src.router import (
    Bundle,
    ResolvedBundle,
    discover_bundle,
    resolve_bundle,
    resolve_catalog,
    select,
)

REPO = Path(__file__).resolve().parents[2]

#: The bundle the examples, the demo and the eval start from when none is named.
DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"


def resolve(
    bundle: Bundle,
    dictionaries: Sequence[Path],
    documents: Sequence[Path],
    readers: Optional[Readers] = None,
) -> ResolvedBundle:
    """Resolve the bundle's data tables against the chosen codebooks and documents.

    One table resolves on its own; several resolve into **one** catalog spanning them,
    so a codebook describing every table is recognised at each of them.
    """
    readers = readers or Readers()
    tables = [create_context(str(p), name=p.stem) for p in bundle.tables]
    sources = [create_context(str(p), name=p.stem) for p in (*dictionaries, *documents)]
    models = {"prose_reader": readers.prose, "claim_comparer": readers.comparer}
    catalog = (
        resolve_catalog(tables[0], sources=sources, **models)
        if len(tables) == 1
        else resolve_bundle(tables, sources=sources, **models)
    )
    return ResolvedBundle(
        bundle.root, bundle.tables, list(dictionaries), list(documents),
        readers.label, catalog,
    )


def resolve_directory(
    path: Path,
    *,
    dictionaries: Optional[List[str]] = None,
    documents: Optional[List[str]] = None,
    readers: Optional[Readers] = None,
) -> ResolvedBundle:
    """Discover a bundle directory, narrow its sources by filename, and resolve it.

    ``dictionaries`` and ``documents`` name files to use; ``None`` uses everything
    discovered and ``["none"]`` uses nothing (:func:`~src.router.bundle.select`).
    Raises ``ValueError`` when the directory holds no bundle — a caller decides whether
    that is a crash, a message, or a prompt.
    """
    if not path.exists() or not any(path.iterdir()):
        raise ValueError(f"Bundle {path} is missing or empty.")
    bundle = discover_bundle(path)
    return resolve(
        bundle,
        select(bundle.codebooks, dictionaries),
        select(bundle.documents, documents),
        readers,
    )
