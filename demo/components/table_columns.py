"""Choosing which optional columns a results table shows.

A results table has a couple of columns that identify a row and several that qualify
it. Which of the latter are worth screen space depends on what the reader is looking
for, and a table that shows all of them scrolls sideways and clips the last. So the
optional set is theirs to pick.

Selections are kept across a switch to another module: Streamlit drops widget state
for anything a run did not render, so a page returned to would otherwise arrive with
every choice reset.
"""

from __future__ import annotations

from typing import Any, Sequence

import streamlit as st


def remembered(key: str, fallback: Any) -> Any:
    """The last value a widget held, surviving a switch away from the page.

    The shadow copy is a plain session-state entry, which Streamlit does not garbage
    collect the way it does widget state.
    """
    return st.session_state.get(f"{key}.kept", fallback)


def remember(key: str, value: Any) -> Any:
    """Record a widget's value for :func:`remembered`, and pass it through."""
    st.session_state[f"{key}.kept"] = value
    return value


def column_chooser(
    optional: Sequence[str],
    *,
    key: str,
    container: Any = None,
    label: str = "Columns shown",
) -> list[str]:
    """Render the chooser and return the optional columns to show.

    Args:
        optional: The columns a reader may hide, in table order.
        key: Session-state key prefix for the widget.
        container: Where to draw. Defaults to the current context.
        label: The widget's label.

    Returns:
        The chosen column names, in the order given by ``optional``.
    """
    target = container if container is not None else st
    chosen = target.multiselect(
        label,
        list(optional),
        default=remembered(f"{key}.columns", list(optional)),
        key=f"{key}.columns",
        help="Hide what you are not reading; the table fits the window instead of "
             "scrolling sideways.",
    )
    remember(f"{key}.columns", chosen)
    return [name for name in optional if name in chosen]
