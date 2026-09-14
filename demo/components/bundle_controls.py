"""Controls for choosing a bundle and the sources within it.

A module page that resolves a bundle picks it and its sources here, and any module
page can show a bundle in the sidebar tree, so these live here rather than in one page
that another has to import from. Production classifies a bundle and uses all of it; these controls exist
so a page can narrow that down and compare inputs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import streamlit as st

from demo.components.bundle_tree import render_bundle_tree
from src.router import NONE, discover_bundle


REPO = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"

# Where to look for bundles to offer in the picker.
BUNDLE_ROOTS = (
    REPO / "data/sample/sharetrait_preprocessed",
    REPO / "data/sample",
    REPO / "data/tests",
)

CUSTOM_PATH = "Custom path…"

#: The two source kinds a bundle carries besides its data tables.
CODEBOOKS, DOCUMENTS = 0, 1
_LABELS = {CODEBOOKS: "Codebooks", DOCUMENTS: "Documents"}


def discover_bundles() -> list[str]:
    """Directories under ``data/`` that hold at least one CSV.

    Not cached: it is a directory listing, and a cached one would hide a bundle
    created while the app is running.

    Returns:
        Repository-relative paths, sorted, for the bundle picker.
    """
    found: set[Path] = set()
    for root in BUNDLE_ROOTS:
        if not root.is_dir():
            continue
        for candidate in [root, *sorted(root.iterdir())]:
            if candidate.is_dir() and any(candidate.glob("*.csv")):
                found.add(candidate)
    return sorted(str(path.relative_to(REPO)) for path in found)


def bundle_picker(action: argparse.Action, key: str) -> Path:
    """Offer the bundles present in the repository, or a path typed by hand.

    Args:
        action: The ``--bundle`` argument, for its help text and default.
        key: Session-state key prefix for the widgets.

    Returns:
        The chosen bundle directory.
    """
    options = discover_bundles()
    default = str(Path(action.default).relative_to(REPO))
    if default not in options:
        options.insert(0, default)

    choice = st.selectbox(
        "Bundle",
        [*options, CUSTOM_PATH],
        index=options.index(default),
        help=action.help,
        key=key,
    )
    if choice == CUSTOM_PATH:
        choice = st.text_input(
            "Bundle path", value=default, key=f"{key}.custom"
        ).strip() or default
    return REPO / choice


def render_tree(bundle: Path) -> None:
    """Show ``bundle``'s classified contents in the sidebar.

    A page picking its bundle passes :func:`selected_bundle` — read from session state
    rather than the form, because the sidebar is drawn before the form runs; a page
    handed a resolution passes the bundle that resolution came from.
    """
    try:
        found = discover_bundle(bundle)
    except ValueError as exc:
        st.sidebar.warning(str(exc))
        return
    render_bundle_tree(bundle, found.tables, found.codebooks, found.documents)


def bundle_sources(bundle: str) -> tuple[list[str], list[str]]:
    """The codebooks and documents auto-discovery finds in ``bundle``.

    Classification reads each CSV's header, so it is cached — but on what the
    directory holds, not just its path. Keyed on the path alone, a file added to or
    edited in the bundle while the app runs would never be offered.
    """
    return _classified_sources(bundle, _listing(bundle))


def _listing(bundle: str) -> tuple[tuple[str, int], ...]:
    """Each file in ``bundle`` with its modification time: what the cache keys on."""
    root = Path(bundle)
    if not root.is_dir():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in root.iterdir() if p.is_file()))


@st.cache_data(show_spinner=False)
def _classified_sources(
    bundle: str, listing: tuple[tuple[str, int], ...]
) -> tuple[list[str], list[str]]:
    """Classify ``bundle``; ``listing`` only keys the cache."""
    try:
        found = discover_bundle(Path(bundle))
    except ValueError:
        return [], []
    return [p.name for p in found.codebooks], [p.name for p in found.documents]


def selected_bundle(key: str) -> str:
    """The bundle the form currently has, so the source pickers can look inside it."""
    chosen = st.session_state.get(f"{key}.bundle")
    if chosen == CUSTOM_PATH:
        chosen = st.session_state.get(f"{key}.bundle.custom") or None
    return str(REPO / chosen) if chosen else str(DEFAULT_BUNDLE)


def source_picker(kind: int):
    """Build an override that ticks off the discovered sources of one kind.

    Production resolves whatever the bundle contains; this page exists to try
    subsets, so it shows what was found and lets each be turned off. Everything is
    selected by default, which is the production behaviour.
    """
    def picker(action: argparse.Action, key: str) -> list[str] | None:
        bundle = selected_bundle(key.rsplit(".", 1)[0])
        options = bundle_sources(bundle)[kind]
        if not options:
            st.caption(f"No {_LABELS[kind].lower()} found in this bundle.")
            return [NONE]
        chosen = st.multiselect(
            _LABELS[kind], options, default=options, help=action.help,
            # A selection belongs to its bundle. Under one key for every bundle, the
            # widget kept a selection whose files the next bundle does not have, and
            # came back empty — silently resolving with none of them.
            key=f"{key}@{bundle}",
        )
        # An empty pick means "use none", which the CLI spells as the NONE token —
        # so the command line shown beside the form reproduces this run exactly.
        return chosen or [NONE]
    return picker
