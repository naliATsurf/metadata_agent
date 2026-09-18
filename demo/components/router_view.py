"""Render a field routing and the plan compiled from it, natively.

The router inverts the pipeline: instead of surveying sources and hoping the schema's
fields fall out, it starts from the fields that must be filled and routes each to
whatever can answer it. What matters when reading a run is therefore per field —
where it routed, on what evidence, and how well grounded — and which fields nothing
could answer, which is the signal the router exists to surface *before* extraction.

**The table follows what decided the routing**, because the two modes produce different
artifacts and reading one as the other is misleading:

- **With the judges on** every candidate is an answer a judge chose, carrying a quote,
  a citation or a tool's bound arguments, and BM25 scored nothing. So one row per field
  states the answer and its evidence, and selecting a row shows that field's full
  working below the table — the same the ``--debug`` flag prints.
- **Without them** the routing *is* the BM25 ranking, rank 1 is the answer by default,
  and the scores are the thing to read. So the ranked candidate rows stay.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from demo.components.table_columns import column_chooser


# Each bucket names where a field's answer comes from.
_BUCKET_HELP = {
    "tool": "Computed by a deterministic tool, from the data itself.",
    "column": "Read from a column, found through the resolved catalog.",
    "document": "Quoted from a document, where the meaning is only stated in prose.",
    "unanswered": "Nothing in the bundle can answer this field.",
}
_ASSURANCE_MARK = {"high": "🟢 high", "medium": "🟡 medium", "low": "🟠 low"}

ANSWER_VIEW, CANDIDATE_VIEW, FIELD_VIEW = "Answers", "Candidates", "Fields"

# One row per field, for a judged plan: what answers it, on what evidence, why, and what
# the router flagged about the answer. The full working is a row's detail panel.
ANSWER_COLUMNS = ("Assurance", "Answer from", "Evidence", "Why", "Checks")

# One row per ranked candidate — the artifact as the router emits it. Ordered by what
# each column describes: the rank and strength of the match, then what was matched and
# where, then the two texts the match was made between.
CANDIDATE_COLUMNS = ("Score", "Kind", "Source", "Query", "Hit")

# Repeated down a field's candidate rows because they belong to the field, not to any
# one candidate — printed once per group so the block reads as a block.
_FIELD_LEVEL = ("Field", "Bucket", "Query")
# One row per field — the summary, where "top candidate" is explicitly the top of a set.
FIELD_COLUMNS = ("Assurance", "Candidates", "Top candidate", "Query")

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
    "Answer from": st.column_config.TextColumn(
        width="medium",
        help="Where the value comes from: a column, a tool with the table and columns "
             "it was bound to, or a document citation.",
    ),
    "Evidence": st.column_config.TextColumn(
        width="large",
        help="The sentence quoted from the document, the column's resolved meaning, or "
             "what the tool computes.",
    ),
    "Why": st.column_config.TextColumn(
        width="large", help="The judge's own reason — including why a field is unanswered.",
    ),
    "Checks": st.column_config.TextColumn(
        width="medium",
        help="What the router flagged: a type or unit that does not fit, a column whose "
             "values vary, a quote it could not locate, a column and a tool disagreeing.",
    ),
}


def render_router_view(result: Any, *, key: str, llm_calls: int = 0) -> None:
    """Render coverage, the per-field routing, and the compiled plan.

    The catalog routed over is not repeated here; it is the catalog resolver page's.

    Args:
        result: The example's ``RouterResult``.
        key: Prefix for this view's widget keys.
        llm_calls: LLM calls the routing made; shown among the tallies when any.
    """
    coverage = result.field_plan.coverage()
    _render_coverage(coverage, result, llm_calls)

    # Keyed, because st.tabs resets to the first tab on every rerun otherwise — so
    # changing a filter inside a tab would bounce the view back to the first one.
    routing_tab, plan_tab = st.tabs(
        ["Field routing", "Compiled plan"],
        key=f"{key}.tab",
        on_change="rerun",
    )
    with routing_tab:
        _render_routings(result.field_plan, key=key)
    with plan_tab:
        _render_plan(result.plan, key)


def _render_coverage(coverage: dict[str, Any], result: Any, llm_calls: int) -> None:
    """The headline: how much of the standard the bundle can answer."""
    total = coverage["total"]
    routed = coverage["routed"]
    unanswered = coverage["unanswered"]

    routed_col, unanswered_col, standard_col, tasks_col, *llm_col = st.columns(
        5 if llm_calls else 4
    )
    if llm_col:
        llm_col[0].metric("LLM calls", llm_calls)
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
    """The router's output, in the shape of whatever decided it.

    A judged plan reads one row per field: the judges chose, so the row states the
    answer, and a selected row opens that field's working. An unjudged plan is a ranked
    set per field which the executor picks from, so a single "source" column would
    assert a decision nobody has made — its rows stay per candidate, with the scores.
    """
    judged = getattr(field_plan, "judged", False)
    buckets = sorted({r.bucket for r in field_plan.routings.values()})
    with st.container(border=True):
        if judged:
            view = ANSWER_VIEW
            search_col, bucket_col = st.columns([2, 2], gap="medium")
        else:
            view_col, search_col, bucket_col = st.columns([1.4, 2, 2], gap="medium")
            view = view_col.segmented_control(
                "View", [CANDIDATE_VIEW, FIELD_VIEW], default=CANDIDATE_VIEW,
                key=f"{key}.view",
                help="Candidates shows every ranked proposal; fields summarises one row "
                     "each.",
            ) or CANDIDATE_VIEW
        query = search_col.text_input(
            "Search fields", placeholder="field path, query, or evidence", key=f"{key}.query"
        ).strip().lower()
        chosen = bucket_col.multiselect(
            "Buckets", buckets, default=[], key=f"{key}.buckets",
            placeholder="all buckets",
            help=" · ".join(f"{b}: {_BUCKET_HELP[b]}" for b in buckets if b in _BUCKET_HELP),
        )
        optional = {
            ANSWER_VIEW: ANSWER_COLUMNS,
            CANDIDATE_VIEW: CANDIDATE_COLUMNS,
            FIELD_VIEW: FIELD_COLUMNS,
        }[view]
        shown = column_chooser(optional, key=f"{key}.{view}")

    build = {
        ANSWER_VIEW: _answer_rows,
        CANDIDATE_VIEW: _candidate_rows,
        FIELD_VIEW: _field_rows,
    }[view]
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
    table = st.dataframe(
        visible,
        width="stretch",
        hide_index=True,
        key=f"{key}.routing_table.{view}",
        height=min(600, 40 + 35 * len(visible)),
        column_config=_ROUTING_COLUMNS,
        on_select="rerun" if view == ANSWER_VIEW else "ignore",
        selection_mode="single-row",
    )
    if view == ANSWER_VIEW:
        selected = getattr(table, "selection", {}).get("rows", [])
        if not selected:
            st.caption("Select a field for the working behind its answer.")
        for index in selected:
            path = visible[index]["Field"]
            with st.container(border=True):
                st.markdown(f"**{path}**")
                _render_working(path, field_plan.routings[path])


def _answer_rows(path: str, routing: Any, shown: list[str]) -> list[dict[str, Any]]:
    """One row per field: the answer a judge chose, and what to distrust about it."""
    available = {
        "Assurance": _ASSURANCE_MARK.get(routing.assurance, ""),
        "Answer from": _answer_of(routing),
        "Evidence": _evidence_of(routing),
        "Why": routing.judge_note or routing.tool_note or "",
        "Checks": " · ".join(_checks(routing)),
    }
    row: dict[str, Any] = {"Field": path, "Bucket": routing.bucket}
    row.update({name: available[name] for name in ANSWER_COLUMNS if name in shown})
    row["_search"] = (
        f"{path} {routing.query} {available['Answer from']} {available['Evidence']}"
    ).lower()
    return [row]


def _more(count: int, unit: str) -> str:
    """`` (+2 more tables)``, or nothing when there is only the one."""
    return f" (+{count} more {unit}{'s' if count > 1 else ''})" if count > 0 else ""


def _answer_of(routing: Any) -> str:
    """Where this field's value comes from, in the terms of its bucket."""
    if not routing.candidates:
        return ""
    top = routing.candidates[0]
    if top.kind == "tool":
        run = routing.tool_arguments[0] if routing.tool_arguments else {}
        columns = ", ".join(f"{name}={value}" for name, value in run.items()
                            if name != "resource")
        where = run.get("resource") or "the whole context"
        return (f"{top.locator} on {where}" + (f" ({columns})" if columns else "")
                + _more(len(routing.tool_arguments) - 1, "table"))
    if top.kind == "quoted_span":
        located = [c for c in routing.citations if c]
        head = located[0] if located else f"{top.resource} (quote not located)"
        return head + _more(len(located) - 1, "citation")
    columns = [c for c in routing.candidates if c.kind == "computed_column"]
    return _locate(top) + _more(len(columns) - 1, "table")


def _evidence_of(routing: Any) -> str:
    """What the answer rests on: the quote, the column's meaning, or the computation."""
    if not routing.candidates:
        return ""
    if routing.judge_quotes:
        return routing.judge_quotes[0] + _more(len(routing.judge_quotes) - 1, "quote")
    return routing.candidates[0].snippet


def _checks(routing: Any) -> list[str]:
    """What the router flagged about this answer — every reason to look closer."""
    notes = [f"does not fit: {reason}" for reason in routing.mismatches]
    notes += [f"varies: {note}" for note in routing.varies]
    if routing.judge_grounded is False:
        notes.append("quote not located")
    kinds = {c.kind for c in routing.candidates}
    if {"tool", "computed_column"} <= kinds:
        notes.append("column and tool disagree")
    if routing.tool_note and "not run:" in routing.tool_note:
        notes.append("tool dropped")
    return notes


def _render_working(path: str, routing: Any) -> None:
    """One field's full working, below the table — what ``--debug`` prints, rendered.

    Everything here is read off the routing artifact, which records each step already;
    an intermediate only a debug flag can show is one the artifact should have carried.
    """
    st.caption(f"{routing.query} · assurance {routing.assurance} · {routing.status}")
    if routing.tool_choice:
        st.markdown(f"**Tool matcher** · `{routing.tool_choice}` — {routing.tool_note or ''}")
    for arguments in routing.tool_arguments:
        bound = ", ".join(f"`{name}` = `{value}`" for name, value in arguments.items())
        st.markdown(f"**Runs with** {bound or 'the whole context'}")
    if routing.judge_note:
        st.markdown(f"**Judge** · `{routing.judge_choice or 'none'}` — {routing.judge_note}")
    for quote, citation in zip(routing.judge_quotes, routing.citations):
        st.markdown(f"> {quote}")
        st.caption(f"`{citation}`" if citation else "not located in the passage")
    for reason in routing.mismatches:
        st.warning(f"does not fit — {reason}", icon="⚠️")
    for note in routing.varies:
        st.caption(f"varies — {note}")
    if routing.candidates:
        st.dataframe(
            [
                {"#": rank, "Kind": c.kind, "Source": _locate(c), "Hit": c.snippet}
                for rank, c in enumerate(routing.candidates, start=1)
            ],
            width="stretch", hide_index=True, column_config=_ROUTING_COLUMNS,
        )
    else:
        st.caption("No candidates — nothing the judges saw answers this field.")


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
