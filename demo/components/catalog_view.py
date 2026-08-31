"""Render a resolved catalog as native Streamlit elements.

The terminal renderer in :mod:`src.router.display` draws the catalog as a Rich
table because that is what a terminal can show. A browser can do better: the
same evidence becomes a sortable table you can filter, with the contested
columns broken out underneath. Both read the resolved columns directly, so
there is no second copy of the catalog's meaning — only a second presentation.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.router import METHOD_LABELS
from src.router.catalog import Catalog, ResolvedColumn


# Confidence as a glyph the eye can scan down a column.
_CONFIDENCE_MARK = {"high": "🟢 high", "medium": "🟡 medium", "low": "🟠 low"}
_UNRESOLVED = "— unresolved"


def render_catalog_view(catalog: Catalog, *, key: str) -> None:
    """Render the summary, the filterable column table, and the conflicts.

    Args:
        catalog: The resolved catalog to display.
        key: Prefix for this view's widget keys.
    """
    columns = catalog.columns
    if not columns:
        st.info("The catalog resolved no columns.")
        return

    _render_summary(columns)
    visible = _render_filters(columns, key=key)
    if not visible:
        st.caption("No columns match the current filters.")
    else:
        _render_table(visible)
    _render_conflicts(columns)


def _render_summary(columns: list[ResolvedColumn]) -> None:
    """Show the headline tallies: how much resolved, how well, how contested."""
    resolved = [c for c in columns if c.link_method != "none"]
    high = [c for c in resolved if c.link_confidence == "high"]
    contested = [c for c in columns if c.conflicts]

    resolved_col, high_col, conflict_col, source_col = st.columns(4)
    resolved_col.metric("Resolved", f"{len(resolved)}/{len(columns)}")
    high_col.metric("High confidence", len(high))
    conflict_col.metric("Conflicts", len(contested))
    source_col.metric(
        "Methods used",
        len({c.link_method for c in resolved}) or 0,
        help=", ".join(
            sorted(METHOD_LABELS.get(c.link_method, c.link_method) for c in resolved)
        )
        or None,
    )

    if resolved:
        st.progress(len(resolved) / len(columns))


def _render_filters(
    columns: list[ResolvedColumn], *, key: str
) -> list[ResolvedColumn]:
    """Render the filter controls and return the columns that survive them."""
    resources = sorted({c.resource for c in columns})
    methods = sorted({c.link_method for c in columns})

    with st.container(border=True):
        search_col, resource_col, method_col = st.columns([2, 1.5, 1.5], gap="medium")
        query = search_col.text_input(
            "Search", placeholder="column name or meaning", key=f"{key}.query"
        ).strip().lower()
        chosen_resources = resource_col.multiselect(
            "Tables", resources, default=[], key=f"{key}.resources",
            placeholder="all tables",
        )
        chosen_methods = method_col.multiselect(
            "Methods",
            methods,
            default=[],
            format_func=lambda m: METHOD_LABELS.get(m, m),
            key=f"{key}.methods",
            placeholder="all methods",
        )
        unresolved_only = st.checkbox(
            "Unresolved only", value=False, key=f"{key}.unresolved"
        )

    def keeps(column: ResolvedColumn) -> bool:
        if chosen_resources and column.resource not in chosen_resources:
            return False
        if chosen_methods and column.link_method not in chosen_methods:
            return False
        if unresolved_only and column.link_method != "none":
            return False
        if query:
            haystack = f"{column.name} {column.description or ''}".lower()
            if query not in haystack:
                return False
        return True

    return [column for column in columns if keeps(column)]


def _render_table(columns: list[ResolvedColumn]) -> None:
    """Show the resolved columns as a sortable table."""
    multi_table = len({c.resource for c in columns}) > 1
    rows = [_row(column, include_table=multi_table) for column in columns]

    config: dict[str, Any] = {
        "Column": st.column_config.TextColumn(width="small", pinned=multi_table),
        "Meaning": st.column_config.TextColumn(width="large"),
        "Units": st.column_config.TextColumn(width="small"),
        "Prior": st.column_config.TextColumn(
            width="small", help="Coarse prior read from the column's own values."
        ),
        "Method": st.column_config.TextColumn(
            width="small", help="How the meaning was resolved."
        ),
        "Confidence": st.column_config.TextColumn(width="small"),
        "Citation": st.column_config.TextColumn(
            width="medium", help="The source the meaning was taken from."
        ),
    }
    if multi_table:
        config["Table"] = st.column_config.TextColumn(width="small")

    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        height=min(600, 40 + 35 * len(rows)),
        column_config=config,
    )


def _row(column: ResolvedColumn, *, include_table: bool) -> dict[str, Any]:
    """Flatten one resolved column into a table row."""
    row: dict[str, Any] = {}
    if include_table:
        row["Table"] = column.resource
    row["Column"] = column.name
    row["Meaning"] = column.description or _UNRESOLVED
    row["Units"] = column.units or ""
    row["Prior"] = column.value_label or ""
    row["Method"] = METHOD_LABELS.get(column.link_method, column.link_method)
    row["Confidence"] = _CONFIDENCE_MARK.get(column.link_confidence, "")
    row["Citation"] = column.link_evidence or ""
    return row


def _render_conflicts(columns: list[ResolvedColumn]) -> None:
    """Break out the columns where sources disagreed, agreed, or lost."""
    contested = [
        c for c in columns if c.conflicts or c.corroborated_by or c.alternatives
    ]
    if not contested:
        return

    st.subheader("Conflicts, corroboration, and alternatives")
    st.caption(
        "Columns where more than one source had something to say. A conflict is "
        "a claim the column's own values contradict."
    )
    for column in contested:
        label = f"{column.resource}.{column.name} — {column.description or 'unresolved'}"
        with st.expander(label, expanded=bool(column.conflicts)):
            for message in column.conflicts:
                st.error(message, icon="⚠️")
            for citation in column.corroborated_by:
                st.success(f"Corroborated by {citation}", icon="✅")
            if column.alternatives:
                st.caption("Candidates that lost")
                st.dataframe(
                    [
                        {
                            "Method": METHOD_LABELS.get(
                                alternative.get("method"), alternative.get("method")
                            ),
                            "Confidence": alternative.get("confidence"),
                            "Meaning": alternative.get("description")
                            or alternative.get("units")
                            or "",
                            "Citation": alternative.get("evidence") or "",
                        }
                        for alternative in column.alternatives
                    ],
                    width="stretch",
                    hide_index=True,
                )
