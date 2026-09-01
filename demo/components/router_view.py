"""Render a field routing and the plan compiled from it, natively.

The router inverts the pipeline: instead of surveying sources and hoping the schema's
fields fall out, it starts from the fields that must be filled and routes each to
whatever can answer it. What matters when reading a run is therefore per field —
where it routed, on what evidence, and how well grounded — and which fields nothing
could answer, which is the signal the router exists to surface *before* extraction.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from demo.components.catalog_view import render_catalog_view


# Each bucket names where a field's answer comes from.
_BUCKET_HELP = {
    "tool": "Computed by a deterministic tool, from the data itself.",
    "column": "Read from a column, found through the resolved catalog.",
    "document": "Quoted from a document, where the meaning is only stated in prose.",
    "unanswered": "Nothing in the bundle can answer this field.",
}
_ASSURANCE_MARK = {"high": "🟢 high", "medium": "🟡 medium", "low": "🟠 low"}


def render_router_view(result: Any, *, key: str) -> None:
    """Render coverage, the per-field routing, the compiled plan, and the catalog.

    Args:
        result: The example's ``RouterResult``.
        key: Prefix for this view's widget keys.
    """
    coverage = result.field_plan.coverage()
    _render_coverage(coverage, result)

    # Keyed, because st.tabs resets to the first tab on every rerun otherwise — so
    # changing a filter inside the catalog tab would bounce the view back to routing.
    routing_tab, plan_tab, catalog_tab = st.tabs(
        ["Field routing", "Compiled plan", "Resolved catalog"],
        key=f"{key}.tab",
        on_change="rerun",
    )
    with routing_tab:
        _render_routings(result.field_plan, key=key)
    with plan_tab:
        _render_plan(result.plan, key)
    with catalog_tab:
        render_catalog_view(result.catalog, key=f"{key}.catalog")


def _render_coverage(coverage: dict[str, Any], result: Any) -> None:
    """The headline: how much of the standard the bundle can answer."""
    total = coverage["total"]
    routed = coverage["routed"]
    unanswered = coverage["unanswered"]

    routed_col, unanswered_col, standard_col, tasks_col = st.columns(4)
    routed_col.metric("Routed", f"{routed}/{total}")
    unanswered_col.metric(
        "Unanswered", len(unanswered),
        help="Fields nothing in the bundle can answer — found before extraction, "
             "not discovered as a confabulation afterwards.",
    )
    standard_col.metric("Standard", result.standard)
    tasks_col.metric("Plan steps", len(result.plan.steps))
    if total:
        st.progress(routed / total)

    if unanswered:
        st.markdown("**Unanswered fields** — " + ", ".join(f"`{f}`" for f in unanswered))


def _render_routings(field_plan: Any, key: str) -> None:
    """One row per schema field: where it routed and on what evidence."""
    rows = [
        {
            "Field": path,
            "Bucket": routing.bucket,
            "Assurance": _ASSURANCE_MARK.get(routing.assurance, ""),
            "Source": _source_of(routing),
            "Extractor": routing.extractor_role or "",
            "Query": routing.query,
        }
        for path, routing in field_plan.routings.items()
    ]

    buckets = sorted({row["Bucket"] for row in rows})
    with st.container(border=True):
        search_col, bucket_col = st.columns([2, 2], gap="medium")
        query = search_col.text_input(
            "Search fields", placeholder="field path or query", key=f"{key}.query"
        ).strip().lower()
        chosen = bucket_col.multiselect(
            "Buckets", buckets, default=[], key=f"{key}.buckets",
            placeholder="all buckets",
            help=" · ".join(f"{b}: {_BUCKET_HELP[b]}" for b in buckets if b in _BUCKET_HELP),
        )

    visible = [
        row
        for row in rows
        if (not chosen or row["Bucket"] in chosen)
        and (not query or query in f"{row['Field']} {row['Query']}".lower())
    ]
    if not visible:
        st.caption("No fields match the current filters.")
        return

    st.dataframe(
        visible,
        width="stretch",
        hide_index=True,
        key=f"{key}.routing_table",
        height=min(600, 40 + 35 * len(visible)),
        column_config={
            "Field": st.column_config.TextColumn(pinned=True),
            "Query": st.column_config.TextColumn(
                width="large", help="The field's description, used as the routing query."
            ),
            "Source": st.column_config.TextColumn(width="medium"),
        },
    )


def _source_of(routing: Any) -> str:
    """The routing's top candidate, as ``resource:locator``."""
    if not routing.candidates:
        return ""
    candidate = routing.candidates[0]
    return (
        f"{candidate.resource}:{candidate.locator}"
        if candidate.resource
        else str(candidate.locator)
    )


def _render_plan(plan: Any, key: str) -> None:
    """The compiled plan: extraction grouped by the table it reads."""
    st.caption(
        "Routing compiled into executable tasks — extraction grouped by the table it "
        "reads, so each table is opened once."
    )
    st.dataframe(
        [
            {
                "#": index,
                "Task": step.task,
                "Player": step.player,
                "Topology": step.topology or "",
                "Scope": ", ".join(step.target_resources or ["<context>"]),
                "Fields": len(step.fields),
            }
            for index, step in enumerate(plan.steps)
        ],
        width="stretch",
        hide_index=True,
        key=f"{key}.plan_table",
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "Fields": st.column_config.NumberColumn(
                width="small", help="How many schema fields this task fills."
            ),
        },
    )
