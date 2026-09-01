"""Controls for choosing a bundle and the sources within it.

Every module page resolves *some* bundle, so the bundle picker, the per-kind source
pickers, and the sidebar tree live here rather than in one page that another has to
import from. Production classifies a bundle and uses all of it; these controls exist
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


REPO = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE = REPO / 'data/sample/sharetrait_preprocessed/TRADAT031'

# Where to look for bundles to offer in the picker.
BUNDLE_ROOTS = (
    REPO / "data/sample/sharetrait_preprocessed",
    REPO / "data/sample",
    REPO / "data/tests",
)

CUSTOM_PATH = "Custom path…"


@st.cache_data(show_spinner=False)
def discover_bundles() -> list[str]:
    """Directories under ``data/`` that hold at least one CSV.

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


def render_tree(key: str) -> None:
    """Show the selected bundle's classified contents in the sidebar.

    Read from session state rather than the form, because the sidebar is drawn
    before the form runs; on the first load nothing is set yet and the parser's
    own default applies.
    """
    bundle = Path(selected_bundle(key))
    try:
        found = discover_bundle(bundle)
    except ValueError as exc:
        st.sidebar.warning(str(exc))
        return
    render_bundle_tree(bundle, found.tables, found.codebooks, found.documents)


@st.cache_data(show_spinner=False)
def bundle_sources(bundle: str) -> tuple[list[str], list[str]]:
    """The codebooks and documents auto-discovery finds in ``bundle``.

    Cached because the page re-runs on every widget change and classification reads
    each CSV's header.
    """
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
        options = bundle_sources(selected_bundle(key.rsplit(".", 1)[0]))[kind]
        if not options:
            st.caption(f"No {action.dest}s found in this bundle.")
            return [NONE]
        chosen = st.multiselect(
            _LABELS[kind], options, default=options, help=action.help, key=key
        )
        # An empty pick means "use none", which the CLI spells as the NONE token —
        # so the command line shown beside the form reproduces this run exactly.
        return chosen or [NONE]
    return picker


_LABELS = {0: "Codebooks", 1: "Documents"}


