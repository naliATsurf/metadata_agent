"""Streamlit page for the catalog resolver example.

Catalog resolution turns opaque column names into described columns by harvesting
meanings from the rest of a bundle. This renders the example's arguments as a form
and its resolved catalog as a table. Its last run is also what the field router page
routes: the resolution is handed on in memory, not resolved again.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from demo import settings as pipeline_settings
from demo.components.arg_form import Defaults
from demo.components.bundle_controls import (
    CODEBOOKS,
    DOCUMENTS,
    bundle_picker,
    render_tree,
    selected_bundle,
    source_picker,
)
from demo.components.catalog_view import render_catalog_view
from demo.components.example_runner import run_example
from src.cli import resolve as resolve_command
from src.router import ResolvedBundle


KEY = "catalog_resolver"

#: Where this page's last run is kept, and so where the router page looks for it.
OUTPUT_KEY = f"{KEY}.output"

#: The name a saved resolution goes by, in a download and in the router's command line.
RESOLUTION_FILE = "catalog.json"


def last_resolution() -> tuple[ResolvedBundle, str] | None:
    """The resolution this page last produced, and the command that produced it.

    ``None`` until a run has succeeded in this session.
    """
    output = st.session_state.get(OUTPUT_KEY) or {}
    if output.get("result") is None:
        return None
    return output["result"], output["command"]


def main() -> None:
    """Render the catalog resolver page."""
    render_tree(Path(selected_bundle(KEY)))
    settings = pipeline_settings.current()
    run_example(
        resolve_command,
        key=KEY,
        script="metadata-agent resolve",
        title="Catalog resolver",
        intro=(
            "Resolve a bundle's columns into described columns, and show the "
            "evidence: how each column was resolved, on what citation, at what "
            "confidence, and anything that conflicted along the way. The field "
            "router routes the last catalog resolved here."
        ),
        overrides={
            "bundle": bundle_picker,
            "dictionary": source_picker(CODEBOOKS),
            "doc": source_picker(DOCUMENTS),
            # Writing a file is a command-line act; the page hands the resolution on
            # in memory and offers it as a download instead.
            "out": lambda action, key: None,
        },
        defaults=Defaults(
            settings.catalog_arguments(),
            token=settings.token(),
            note=pipeline_settings.FORM_NOTE,
        ),
        render=_render,
        # The model and the prompt log belong to the LLM reader; without it they do nothing.
        enabled_by={"LLM prose reader model": "llm_reader", "debug": "llm_reader"},
    )


def _render(resolved: ResolvedBundle, llm_calls: int) -> None:
    render_catalog_view(resolved.catalog, key=KEY, llm_calls=llm_calls)
    st.download_button(
        "Download resolution",
        data=json.dumps(resolved.to_dict(), indent=1, default=str),
        file_name=RESOLUTION_FILE,
        mime="application/json",
        key=f"{KEY}.download_resolution",
        help="The catalog and the files it was resolved from — what "
             "`metadata-agent route --catalog` reads.",
    )


if __name__ == "__main__":
    main()
