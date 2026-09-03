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
from demo.components.table_columns import column_chooser


# Each bucket names where a field's answer comes from.
_BUCKET_HELP = {
    "tool": "Computed by a deterministic tool, from the data itself.",
    "column": "Read from a column, found through the resolved catalog.",
    "document": "Quoted from a document, where the meaning is only stated in prose.",
    "unanswered": "Nothing in the bundle can answer this field.",
}
_ASSURANCE_MARK = {"high": "🟢 high", "medium": "🟡 medium", "low": "🟠 low"}

CANDIDATE_VIEW, FIELD_VIEW = "Candidates", "Fields"

# One row per ranked candidate — the artifact as the router emits it. Ordered by what
# each column describes: the rank and strength of the match, then what was matched and
# where, then the two texts the match was made between.
CANDIDATE_COLUMNS = ("Score", "Kind", "Source", "Query", "Hit")

# Repeated down a field's candidate rows because they belong to the field, not to any
# one candidate — printed once per group so the block reads as a block.
_FIELD_LEVEL = ("Field", "Bucket", "Query")
# One row per field — the summary, where "top candidate" is explicitly the top of a set.
FIELD_COLUMNS = ("Assurance", "Candidates", "Top candidate", "Extractor", "Query")

_ROUTING_COLUMNS = {
    "Field": st.column_config.TextColumn(pinned=True),
    "#": st.column_config.NumberColumn(
        width="small", format="%d", help="Rank within this field's candidate set."
    ),
    "Query": st.column_config.TextColumn(
        width="medium", help="The field's description, used as the routing query."
    ),
    "Hit": st.column_config.TextColumn(
        width="large",
        help="What this candidate says — the text the query was matched against.",
    ),
    "Score": st.column_config.NumberColumn(
        width="small", format="%.2f",
        help="BM25 score. Comparable within a field, not across fields.",
    ),
    "Kind": st.column_config.TextColumn(
        width="small", help="What sort of location this candidate is.",
    ),
    "Source": st.column_config.TextColumn(width="medium"),
    "Bucket": st.column_config.TextColumn(
        width="small",
        help="A property of the *field*, not of any one candidate: which mechanism "
             "will produce its value. Read off the top candidate's kind, so it is "
             "shown once per field.",
    ),
    "Top candidate": st.column_config.TextColumn(
        width="medium", help="Rank 1 of the set — a proposal, not a decision.",
    ),
    "Candidates": st.column_config.NumberColumn(
        width="small", help="How many sources the router proposed for this field.",
    ),
}


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
    """The router's output, either summarised per field or in full per candidate.

    The router proposes a *ranked set* per field and the executor picks from it, so a
    single "source" column would assert a decision nobody has made. The candidate view
    is the artifact as it stands; the field view is the summary over it.
    """
    buckets = sorted({r.bucket for r in field_plan.routings.values()})
    with st.container(border=True):
        view_col, search_col, bucket_col = st.columns([1.4, 2, 2], gap="medium")
        view = view_col.segmented_control(
            "View", [CANDIDATE_VIEW, FIELD_VIEW], default=CANDIDATE_VIEW,
            key=f"{key}.view",
            help="Candidates shows every ranked proposal; fields summarises one row "
                 "each.",
        ) or CANDIDATE_VIEW
        query = search_col.text_input(
            "Search fields", placeholder="field path, query, or hit", key=f"{key}.query"
        ).strip().lower()
        chosen = bucket_col.multiselect(
            "Buckets", buckets, default=[], key=f"{key}.buckets",
            placeholder="all buckets",
            help=" · ".join(f"{b}: {_BUCKET_HELP[b]}" for b in buckets if b in _BUCKET_HELP),
        )
        optional = CANDIDATE_COLUMNS if view == CANDIDATE_VIEW else FIELD_COLUMNS
        shown = column_chooser(optional, key=f"{key}.{view}")

    build = _candidate_rows if view == CANDIDATE_VIEW else _field_rows
    rows = [
        row
        for path, routing in field_plan.routings.items()
        for row in build(path, routing, shown)
    ]
    visible = [
        row
        for row in rows
        if (not chosen or row["Bucket"] in chosen) and (not query or query in row["_search"])
    ]
    for row in visible:
        row.pop("_search", None)
    if view == CANDIDATE_VIEW:
        _blank_repeats(visible)
    if not visible:
        st.caption("No fields match the current filters.")
        return

    st.caption(
        f"{len(visible)} candidates across {len(field_plan.routings)} fields — "
        "ranked proposals, not decisions. The executor picks one per field."
        if view == CANDIDATE_VIEW
        else f"{len(visible)} fields."
    )
    st.dataframe(
        visible,
        width="stretch",
        hide_index=True,
        key=f"{key}.routing_table.{view}",
        height=min(600, 40 + 35 * len(visible)),
        column_config=_ROUTING_COLUMNS,
    )


def _blank_repeats(rows: list[dict[str, Any]]) -> None:
    """Print a field's own columns only on the first row of its run of candidates.

    The rows for one field are already adjacent; blanking the repeats is what makes
    that visible, so a five-candidate field reads as one block rather than five
    unrelated rows. Sorting the table by another column breaks the runs — the values
    are still correct per row, they are simply no longer grouped.
    """
    previous: tuple[Any, ...] | None = None
    for row in rows:
        current = tuple(row.get(name) for name in _FIELD_LEVEL)
        if current == previous:
            for name in _FIELD_LEVEL:
                if name in row:
                    row[name] = ""
        previous = current


def _candidate_rows(path: str, routing: Any, shown: list[str]) -> list[dict[str, Any]]:
    """Every ranked candidate for one field, one row each.

    A field with no candidate still gets a row, so an unanswered field is visible
    here rather than silently absent.
    """
    if not routing.candidates:
        row: dict[str, Any] = {"Field": path, "Bucket": routing.bucket, "#": None}
        row.update({n: None for n in CANDIDATE_COLUMNS if n in shown})
        if "Query" in shown:
            row["Query"] = routing.query
        row["_search"] = f"{path} {routing.query}".lower()
        return [row]

    rows = []
    for rank, candidate in enumerate(routing.candidates, start=1):
        available = {
            "Score": round(candidate.score, 2),
            "Kind": candidate.kind,
            "Source": _locate(candidate),
            "Query": routing.query,
            "Hit": candidate.snippet,
        }
        row = {"Field": path, "Bucket": routing.bucket, "#": rank}
        row.update({n: available[n] for n in CANDIDATE_COLUMNS if n in shown})
        row["_search"] = f"{path} {routing.query} {candidate.snippet}".lower()
        rows.append(row)
    return rows


def _field_rows(path: str, routing: Any, shown: list[str]) -> list[dict[str, Any]]:
    """One row summarising a field's routing."""
    available = {
        "Assurance": _ASSURANCE_MARK.get(routing.assurance, ""),
        "Candidates": len(routing.candidates),
        "Top candidate": _source_of(routing),
        "Extractor": routing.extractor_role or "",
        "Query": routing.query,
    }
    row: dict[str, Any] = {"Field": path, "Bucket": routing.bucket}
    row.update({name: available[name] for name in FIELD_COLUMNS if name in shown})
    row["_search"] = f"{path} {routing.query} {_hit_of(routing)}".lower()
    return [row]


def _locate(candidate: Any) -> str:
    """One candidate as ``resource:locator``."""
    return (
        f"{candidate.resource}:{candidate.locator}"
        if candidate.resource
        else str(candidate.locator)
    )


def _hit_of(routing: Any) -> str:
    """The winning candidate's own text — the document the query was ranked against.

    Routing is a lexical match between the field's description and this snippet, so
    putting the two side by side is what makes a match judgeable: a tool's declared
    purpose, a column's name and resolved meaning, or the retrieved span.
    """
    return routing.candidates[0].snippet if routing.candidates else ""


def _source_of(routing: Any) -> str:
    """The routing's top candidate, as ``resource:locator``."""
    if not routing.candidates:
        return ""
    candidate = routing.candidates[0]
    return _locate(candidate)


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
