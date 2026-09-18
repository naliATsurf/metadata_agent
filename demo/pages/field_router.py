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
from src.cli import route as route_command
from src.pipelines.field_driven import FieldDrivenRun
from src.router import NONE, ResolvedBundle


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
        route_command,
        key=KEY,
        script="metadata-agent route",
        title=TITLE,
        intro=(
            "Fill a metadata standard field by field: route each schema field of the "
            "resolved catalog to whatever answers it, and compile the routing into a "
            "plan whose extraction is grouped per table. A field nothing can answer is "
            "flagged here, before extraction."
        ),
        overrides={
            "catalog": _catalog_input(resolved),
            "search_doc": _document_picker(resolved),
        },
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


def _document_picker(resolved: ResolvedBundle):
    """The ``--search-doc`` widget: which of the resolution's documents are searched.

    The options are the *resolution's* documents, not the bundle's, because the router
    cannot route a file the resolver was never given — narrowing here re-routes in
    memory, where narrowing on the resolver page would resolve the catalog again.

    Everything is selected by default, which is the production behaviour. Turning some
    off is how a bundle carrying rival variants of one README — a short one, a prose
    rewrite, a whole methods section — is routed against one of them at a time.
    """
    def widget(action, key) -> list[str] | None:
        options = [path.name for path in resolved.documents]
        if not options:
            st.caption(
                "This resolution carries no documents — fields no column answers "
                "have nothing to route against."
            )
            return [NONE]
        chosen = st.multiselect(
            "Documents", options, default=options, help=action.help,
            # Keyed by resolution root for the same reason the resolver's picker is
            # keyed by bundle: a selection belongs to the files it was made against.
            key=f"{key}@{resolved.root}",
        )
        return chosen or [NONE]
    return widget


def _render(
    result: FieldDrivenRun, current: ResolvedBundle, llm_calls: int
) -> None:
    if result.resolved is not current:
        st.warning(
            "The catalog has been resolved again since this routing ran. "
            "Run again to route the current catalog."
        )
    render_router_view(result, key=KEY, llm_calls=llm_calls)


if __name__ == "__main__":
    main()
