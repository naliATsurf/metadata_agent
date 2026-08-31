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

from demo.components.catalog_view import render_catalog_view
from demo.components.example_runner import run_example
from examples import resolve_catalog


REPO = Path(__file__).resolve().parents[2]

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


def main() -> None:
    """Render the catalog resolver page."""
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
        overrides={"bundle": bundle_picker},
        render=lambda catalog: render_catalog_view(catalog, key="catalog_resolver"),
    )


if __name__ == "__main__":
    main()
