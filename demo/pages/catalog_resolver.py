"""Streamlit page for the catalog resolver example.

Catalog resolution turns opaque column names into described columns by
harvesting meanings from the rest of a bundle. The example script already
prints that evidence for a terminal; this page renders the same script's
arguments as a form and shows the same output, so what a viewer sees here is
what the terminal shows.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import streamlit as st

from demo.components.bundle_tree import render_bundle_tree
from demo.components.catalog_view import render_catalog_view
from demo.components.example_runner import run_example
from examples import resolve_catalog
from examples.resolve_catalog import NONE, discover


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


def _render_tree() -> None:
    """Show the selected bundle's classified contents in the sidebar.

    Read from session state rather than the form, because the sidebar is drawn
    before the form runs; on the first load nothing is set yet and the parser's
    own default applies.
    """
    bundle = Path(_selected_bundle("catalog_resolver"))
    try:
        tables, codebooks, documents = discover(bundle)
    except SystemExit as exc:
        st.sidebar.warning(str(exc))
        return
    render_bundle_tree(bundle, tables, codebooks, documents)


@st.cache_data(show_spinner=False)
def _sources(bundle: str) -> tuple[list[str], list[str]]:
    """The codebooks and documents auto-discovery finds in ``bundle``.

    Cached because the page re-runs on every widget change and classification reads
    each CSV's header.
    """
    try:
        _, codebooks, documents = discover(Path(bundle))
    except SystemExit:
        return [], []
    return [p.name for p in codebooks], [p.name for p in documents]


def _selected_bundle(key: str) -> str:
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
        options = _sources(_selected_bundle(key.rsplit(".", 1)[0]))[kind]
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


def main() -> None:
    """Render the catalog resolver page."""
    _render_tree()
    run_example(
        resolve_catalog,
        key="catalog_resolver",
        script="examples/resolve_catalog.py",
        title="Catalog resolver",
        intro=(
            "Resolve a bundle's columns into described columns, and show the "
            "evidence: how each column was resolved, on what citation, at what "
            "confidence, and anything that conflicted along the way."
        ),
        overrides={
            "bundle": bundle_picker,
            "dictionary": source_picker(0),
            "doc": source_picker(1),
        },
        render=lambda catalog: render_catalog_view(catalog, key="catalog_resolver"),
    )


if __name__ == "__main__":
    main()
