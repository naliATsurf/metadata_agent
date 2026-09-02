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

from demo.components.table_columns import column_chooser, remember, remembered
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
    matching = _render_filters(columns, key=key)
    if not matching:
        st.caption("No columns match the current filters.")
    else:
        _render_table(
            matching,
            st.session_state.get(f"{key}.columns", list(OPTIONAL_COLUMNS)),
            key,
        )
    _render_conflicts(columns, key)


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
            "Search columns", placeholder="column name or meaning",
            value=remembered(f"{key}.query", ""), key=f"{key}.query",
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
        control_col, column_col = st.columns([1, 3], gap="medium")
        unresolved_only = control_col.checkbox(
            "Unresolved only", value=False, key=f"{key}.unresolved"
        )
        column_chooser(OPTIONAL_COLUMNS, key=key, container=column_col)

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

    remember(f"{key}.query", query)
    return [column for column in columns if keeps(column)]


# Always shown: the column and what it was resolved to. The rest are optional, so a
# narrow window can be given to the ones being read rather than clipping the last.
OPTIONAL_COLUMNS = ("Source text", "Units", "Prior", "Method", "Confidence", "Citation")

# Only the two prose-bearing columns get a fixed width. The rest size to their content,
# so the grid fits the container instead of overflowing a fixed total and scrolling.
_WIDTHS = {"Meaning": "medium", "Source text": "large"}

_HELP = {
    "Prior": "Coarse prior read from the column's own values.",
    "Method": "How the meaning was resolved.",
    "Citation": "Where the meaning was taken from.",
    "Source text": "What that source actually says — the codebook row, or the "
                   "sentence located in the document.",
}


def _render_table(columns: list[ResolvedColumn], visible: list[str], key: str) -> None:
    """Show the resolved columns as a sortable table.

    The explicit ``key`` matters: without one Streamlit identifies a dataframe by its
    position in the render tree, so switching to another module reuses this element
    for a differently shaped table and the frontend keeps the previous one's column
    sizing and sort state.
    """
    multi_table = len({c.resource for c in columns}) > 1
    rows = [
        _row(column, include_table=multi_table, visible=visible) for column in columns
    ]
    if not rows:
        return

    config: dict[str, Any] = {}
    for name in rows[0]:
        config[name] = st.column_config.TextColumn(
            width=_WIDTHS.get(name),
            help=_HELP.get(name),
            # Keep the identifying columns in view while the rest scroll.
            pinned=name in ("Table", "Column"),
        )

    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        height=min(600, 40 + 35 * len(rows)),
        column_config=config,
        key=f"{key}.table",
    )


def _row(
    column: ResolvedColumn, *, include_table: bool, visible: list[str]
) -> dict[str, Any]:
    """Flatten one resolved column into a table row, keeping only wanted columns."""
    available = {
        "Source text": column.link_quote or "",
        "Units": column.units or "",
        "Prior": column.value_label or "",
        "Method": METHOD_LABELS.get(column.link_method, column.link_method),
        "Confidence": _CONFIDENCE_MARK.get(column.link_confidence, ""),
        "Citation": column.link_evidence or "",
    }
    row: dict[str, Any] = {}
    if include_table:
        row["Table"] = column.resource
    row["Column"] = column.name
    row["Meaning"] = column.description or _UNRESOLVED
    for name in OPTIONAL_COLUMNS:
        if name in visible:
            row[name] = available[name]
    return row


def _render_conflicts(columns: list[ResolvedColumn], key: str) -> None:
    """Break out the columns where sources disagreed, agreed, or lost."""
    contested = [
        c for c in columns if c.conflicts or c.corroborated_by or c.alternatives
    ]
    if not contested:
        return

    st.subheader("Conflicts, corroboration, and alternatives")
    st.caption(
        "Columns where more than one source had something to say. A conflict is a "
        "claim the column's own values contradict — the resolver catching it is the "
        "system working, not an error."
    )
    for column in contested:
        label = f"{column.resource}.{column.name} — {column.description or 'unresolved'}"
        with st.expander(label, expanded=bool(column.conflicts)):
            if column.link_quote:
                st.caption(f"Cited from {column.link_evidence}")
                st.markdown(f"> {column.link_quote}")
            # Deliberately not st.error/st.success. A conflict is a finding — the
            # value profile caught a claim the data refutes — not a failure, and
            # alarm colouring pulls attention to the part that worked. Red is kept
            # for a run that actually broke.
            for message in column.conflicts:
                st.markdown(f"**Conflict** — {message}")
            for citation in column.corroborated_by:
                st.markdown(f"**Corroborated by** {citation}")
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
                            "Source text": alternative.get("quote") or "",
                        }
                        for alternative in column.alternatives
                    ],
                    width="stretch",
                    hide_index=True,
                    key=f"{key}.alt.{column.resource}.{column.name}",
                )
