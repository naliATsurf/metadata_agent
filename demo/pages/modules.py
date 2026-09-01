"""Streamlit page hosting the individual module demos.

The pipeline page shows the agent end to end. This page is the other half: the
modules it is built from, each one a front end for the example script that
already exercises it. They live behind a single navigation entry so the sidebar
stays short as more of them arrive — adding one is a line in :data:`MODULES`.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from demo.pages import catalog_resolver, field_router


# Module label -> the function that renders it. Order is the order shown.
MODULES: dict[str, Callable[[], None]] = {
    "Catalog resolver": catalog_resolver.main,
    "Field router": field_router.main,
}

_SELECTION_KEY = "modules.selected"


def main() -> None:
    """Render the module picker and the chosen module."""
    labels = list(MODULES)
    if not labels:
        st.info("No module demos are registered yet.")
        return

    selected = st.session_state.get(_SELECTION_KEY)
    if selected not in labels:
        selected = labels[0]

    if len(labels) > 1:
        selected = st.segmented_control(
            "Module",
            labels,
            default=selected,
            key=_SELECTION_KEY,
            label_visibility="collapsed",
        ) or labels[0]

    # A container keyed by module: Streamlit matches elements by position, so without
    # a distinct subtree the next module's widgets inherit the previous one's frontend
    # state — a table keeping the old column layout, most visibly.
    slug = selected.lower().replace(" ", "_")
    with st.container(key=f"module_{slug}"):
        MODULES[selected]()


if __name__ == "__main__":
    main()
