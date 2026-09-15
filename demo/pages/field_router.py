"""Streamlit page for the field router example.

The router starts from the schema's fields and routes each to whatever can answer
it, then compiles that routing into an executable plan. It routes a catalog the
catalog resolver page already resolved, so this page is the router alone: until that
page has run, there is nothing here to route.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from demo import settings as pipeline_settings
from demo.components.arg_form import Defaults
from demo.components.bundle_controls import render_tree
from demo.components.example_runner import run_example
from demo.components.router_view import render_router_view
from demo.pages import catalog_resolver
from examples import field_router_plan
from src.router import ResolvedBundle


KEY = "field_router"
TITLE = "Field router"


def main() -> None:
    """Render the field router page."""
    upstream = catalog_resolver.last_resolution()
    if upstream is None:
        st.title(TITLE)
        st.info(
            "The router routes a resolved catalog. Run the **Catalog resolver** "
            "first; its catalog, and the bundle files it came from, are routed here."
        )
        return

    resolved, resolver_command = upstream
    render_tree(resolved.root)
    settings = pipeline_settings.current()
    run_example(
        field_router_plan,
        key=KEY,
        script="examples/field_router_plan.py",
        title=TITLE,
        intro=(
            "Fill a metadata standard field by field: route each schema field of the "
            "resolved catalog to whatever answers it, and compile the routing into a "
            "plan whose extraction is grouped per table. A field nothing can answer is "
            "flagged here, before extraction."
        ),
        overrides={"catalog": _catalog_input(resolved)},
        defaults=Defaults(
            settings.router_arguments(),
            token=settings.token(),
            note=pipeline_settings.FORM_NOTE,
        ),
        inputs={"resolved": resolved},
        preceding_command=f"{resolver_command} --out {catalog_resolver.RESOLUTION_FILE}",
        render=lambda result, llm_calls: _render(result, resolved, llm_calls),
        layout=[["Input", "Metadata standard", "Routing"], ["LLM candidate judge model"]],
        enabled_by={"LLM candidate judge model": "llm_candidate_judge"},
    )


def _catalog_input(resolved: ResolvedBundle):
    """The ``--catalog`` widget: not a choice, a statement of what will be routed."""
    def widget(action, key) -> Path:
        catalog = resolved.catalog
        described = sum(1 for c in catalog.columns if c.link_method != "none")
        st.markdown(f"**Catalog** — `{resolved.root.name}`")
        st.caption(
            f"From the catalog resolver: {described}/{len(catalog.columns)} columns "
            f"described across {len(resolved.tables)} tables · prose reader "
            f"{resolved.reader} · documents "
            f"{', '.join(p.name for p in resolved.documents) or 'none'}"
        )
        # What the command line names; the run itself is handed the resolution.
        return Path(catalog_resolver.RESOLUTION_FILE)
    return widget


def _render(
    result: field_router_plan.RouterResult, current: ResolvedBundle, llm_calls: int
) -> None:
    if result.resolved is not current:
        st.warning(
            "The catalog has been resolved again since this routing ran. "
            "Run again to route the current catalog."
        )
    render_router_view(result, key=KEY, llm_calls=llm_calls)


if __name__ == "__main__":
    main()
