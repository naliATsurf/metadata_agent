"""A side panel showing what a bundle contains, and how it was classified.

The resolver decides for itself which files are data, which are codebooks, and which
are documents. This renders that decision so a viewer can see the input the same way
the resolver does — before any resolving happens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import streamlit as st

from src.context import create_context


_KINDS = (
    ("Data tables", "📊", "Resolved column by column."),
    ("Codebooks", "📖", "Recognised by shape: a column of the bundle's column names."),
    ("Documents", "📄", "Searched, and read when a prose tier is enabled."),
)


@st.cache_data(show_spinner=False)
def describe(path: str, mtime: float) -> dict[str, Any]:
    """One resource's headline facts, keyed by path and mtime so edits invalidate it.

    Args:
        path: The file to describe.
        mtime: The file's modification time, part of the cache key only.

    Returns:
        A dict with the resource's own summary line and, for tabular resources,
        its column names.
    """
    context = create_context(path, name=Path(path).stem)
    info = context.get_resource_info(context.resources[0])
    return {
        # Each ResourceInfo subclass renders its own vocabulary, so this needs no
        # branching on modality.
        "summary": info.summary(),
        "fields": list(getattr(info, "field_names", []) or []),
    }


def render_bundle_tree(
    bundle: Path,
    tables: Iterable[Path],
    codebooks: Iterable[Path],
    documents: Iterable[Path],
    *,
    container: Any = None,
) -> None:
    """Render the bundle as a tree of classified files.

    Args:
        bundle: The bundle directory, shown as the root.
        tables: Files classified as data tables.
        codebooks: Files classified as codebooks.
        documents: Files classified as documents.
        container: Where to draw. Defaults to the sidebar.
    """
    target = container if container is not None else st.sidebar
    groups = (list(tables), list(codebooks), list(documents))

    with target:
        st.markdown(f"**{bundle.name}**")
        st.caption(_short(bundle))
        for (title, icon, blurb), paths in zip(_KINDS, groups):
            st.markdown(f"{icon} **{title}** · {len(paths)}")
            if not paths:
                st.caption("none")
                continue
            st.caption(blurb)
            for path in paths:
                _render_file(path)


def _short(path: Path) -> str:
    """The path as someone would type it, relative to the working directory."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _render_file(path: Path) -> None:
    """One file: its summary line, and its columns when it has them."""
    try:
        facts = describe(str(path), path.stat().st_mtime)
    except Exception as exc:  # noqa: BLE001 — a bad file should not blank the panel
        st.caption(f"{path.name} — unreadable ({type(exc).__name__})")
        return

    with st.expander(path.name):
        st.caption(facts["summary"])
        if facts["fields"]:
            st.markdown(
                "\n".join(f"- `{name}`" for name in facts["fields"])
            )
