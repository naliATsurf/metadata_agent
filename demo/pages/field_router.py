"""Streamlit page for the field router example.

The router starts from the schema's fields and routes each to whatever can answer
it, then compiles that routing into an executable plan. This page runs the example
that exercises that path and renders what it produced.
"""

from __future__ import annotations

from demo.components.bundle_controls import (
    CODEBOOKS,
    DOCUMENTS,
    bundle_picker,
    render_tree,
    source_picker,
)
from demo.components.example_runner import run_example
from demo.components.router_view import render_router_view
from examples import field_router_plan


KEY = "field_router"


def main() -> None:
    """Render the field router page."""
    render_tree(KEY)
    run_example(
        field_router_plan,
        key=KEY,
        script="examples/field_router_plan.py",
        title="Field router",
        intro=(
            "Fill a metadata standard field by field: resolve every table into one "
            "catalog, route each schema field to whatever answers it, and compile the "
            "routing into a plan whose extraction is grouped per table. A field "
            "nothing can answer is flagged here, before extraction."
        ),
        overrides={
            "bundle": bundle_picker,
            "dictionary": source_picker(CODEBOOKS),
            "doc": source_picker(DOCUMENTS),
        },
        render=lambda result: render_router_view(result, key=KEY),
    )


if __name__ == "__main__":
    main()
