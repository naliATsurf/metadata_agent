"""Streamlit page for the catalog resolver example.

Catalog resolution turns opaque column names into described columns by harvesting
meanings from the rest of a bundle. This renders the example's arguments as a form
and its resolved catalog as a table.
"""

from __future__ import annotations

from demo.components.bundle_controls import (
    CODEBOOKS,
    DOCUMENTS,
    bundle_picker,
    render_tree,
    source_picker,
)
from demo.components.catalog_view import render_catalog_view
from demo.components.example_runner import run_example
from examples import resolve_catalog


KEY = "catalog_resolver"


def main() -> None:
    """Render the catalog resolver page."""
    render_tree(KEY)
    run_example(
        resolve_catalog,
        key=KEY,
        script="examples/resolve_catalog.py",
        title="Catalog resolver",
        intro=(
            "Resolve a bundle's columns into described columns, and show the "
            "evidence: how each column was resolved, on what citation, at what "
            "confidence, and anything that conflicted along the way."
        ),
        overrides={
            "bundle": bundle_picker,
            "dictionary": source_picker(CODEBOOKS),
            "doc": source_picker(DOCUMENTS),
        },
        render=lambda catalog: render_catalog_view(catalog, key=KEY),
    )


if __name__ == "__main__":
    main()
