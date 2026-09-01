"""Run an ``examples/`` script from a Streamlit page.

Every example follows the same contract — ``build_parser()`` describes its
arguments and ``run(args, console)`` does the work, writing through the console
it is handed. That is enough for one generic page: render the parser as a form,
call ``run`` with a recording console, and show what it printed.

A page therefore adds an example without restating its arguments or reproducing
its output format.
"""

from __future__ import annotations

import argparse
from types import ModuleType
from typing import Any, Callable

import streamlit as st

from demo.components.arg_form import (
    WidgetOverride,
    command_line,
    render_form,
)
from demo.components.console_view import (
    DEFAULT_WIDTH,
    console_html,
    console_text,
    recording_console,
    render_console_html,
)


def run_example(
    module: ModuleType,
    *,
    key: str,
    script: str,
    title: str,
    intro: str | None = None,
    overrides: dict[str, WidgetOverride] | None = None,
    columns: int = 2,
    render: Callable[[Any], None] | None = None,
) -> None:
    """Render the form, run the example, and show its console output.

    Args:
        module: The example module, exposing ``build_parser`` and ``run``.
        key: Prefix for this page's session-state keys.
        script: Path of the script, shown in the equivalent command line.
        title: Page heading.
        intro: Optional paragraph under the heading. Defaults to the parser's
            own description.
        overrides: Per-argument widget replacements, keyed by ``dest``.
        columns: How many columns to lay the argument widgets out in.
        render: Optional renderer for whatever ``run()`` returned. Given one,
            the page shows it and keeps the printed output as a fallback;
            without one, the printed output is all there is to show.
    """
    parser = module.build_parser()

    st.title(title)
    st.caption(intro or parser.description or "")

    args = _render_arguments(parser, key=key, overrides=overrides, columns=columns)

    st.code(command_line(parser, args, script=script), language="bash")

    if render is None:
        # With no native renderer the printed output is the whole result, so the
        # width it was rendered at is worth exposing.
        control, width_control = st.columns(
            [2, 1], gap="medium", vertical_alignment="bottom"
        )
        with width_control:
            width = st.slider(
                "Output width", min_value=80, max_value=240,
                value=DEFAULT_WIDTH, step=10, key=f"{key}.width",
                help="Character width Rich renders to, as a terminal would.",
            )
    else:
        control, width = st.container(), DEFAULT_WIDTH
    with control:
        clicked = st.button("Run", type="primary", width="stretch", key=f"{key}.run")

    if clicked:
        st.session_state[f"{key}.output"] = _execute(module, args, width)

    _render_output(st.session_state.get(f"{key}.output"), key=key, render=render)


def _render_arguments(
    parser: argparse.ArgumentParser,
    *,
    key: str,
    overrides: dict[str, WidgetOverride] | None,
    columns: int,
) -> argparse.Namespace:
    """Render the form, one bordered section per argument group.

    argparse groups are part of the argument surface — ``--help`` prints them — so the
    form follows them rather than inventing its own arrangement. A parser that defines
    no groups renders as a single section, as before.
    """
    populated = [
        (group, _visible_actions(group))
        for group in _argument_groups(parser)
        if _visible_actions(group)
    ]
    values: dict[str, Any] = {}

    # Groups side by side, each one's arguments stacked under its title: the form
    # stays one screen tall instead of scrolling. A parser with no groups of its own
    # falls back to spreading its arguments across `columns`.
    if len(populated) > 1:
        for (group, actions), column in zip(
            populated, st.columns(len(populated), gap="medium")
        ):
            with column, st.container(border=True):
                _render_group_heading(group)
                values.update(vars(_render_actions(actions, key, overrides, 1)))
        return argparse.Namespace(**values)

    for group, actions in populated:
        with st.container(border=True):
            _render_group_heading(group)
            values.update(vars(_render_actions(actions, key, overrides, columns)))
    return argparse.Namespace(**values)


def _render_group_heading(group: Any) -> None:
    """Title and blurb for a group argparse did not invent itself."""
    if group.title and group.title.lower() not in _DEFAULT_GROUP_TITLES:
        st.markdown(f"**{group.title}**")
        if group.description:
            st.caption(group.description)


# argparse's own groups, which carry no meaning for a form.
_DEFAULT_GROUP_TITLES = {"positional arguments", "options", "optional arguments"}


def _argument_groups(parser: argparse.ArgumentParser) -> list[Any]:
    """The parser's argument groups, in declaration order."""
    return list(parser._action_groups)


def _visible_actions(group: Any) -> list[argparse.Action]:
    """The group's arguments, minus the ones a form should not show."""
    return [
        action
        for action in group._group_actions
        if not isinstance(action, (argparse._HelpAction, argparse._VersionAction))
    ]


def _render_actions(
    actions: list[argparse.Action],
    key: str,
    overrides: dict[str, WidgetOverride] | None,
    columns: int,
) -> argparse.Namespace:
    """Lay one group's arguments out across ``columns`` and collect their values."""
    if columns <= 1 or len(actions) == 1:
        return render_form(
            _parser_over(actions), key_prefix=key, overrides=overrides
        )

    groups: list[list[argparse.Action]] = [[] for _ in range(columns)]
    for index, action in enumerate(actions):
        groups[index % columns].append(action)

    values: dict[str, Any] = {}
    for subset, column in zip(groups, st.columns(columns, gap="large")):
        if not subset:
            continue
        with column:
            namespace = render_form(
                _parser_over(subset), key_prefix=key, overrides=overrides
            )
            values.update(vars(namespace))
    return argparse.Namespace(**values)


def _parser_over(actions: list[argparse.Action]) -> argparse.ArgumentParser:
    """A view holding only ``actions``.

    The actions themselves are shared, so their types, defaults, and help text are the
    originals; only the iteration order is narrowed.
    """
    view = argparse.ArgumentParser(add_help=False)
    view._actions = list(actions)
    return view


def _execute(module: ModuleType, args: argparse.Namespace, width: int) -> dict[str, Any]:
    """Run the example, capturing its console output and any failure."""
    console = recording_console(width=width)
    error: str | None = None
    result: Any = None
    with st.spinner("Running…"):
        try:
            result = module.run(args, console)
        except SystemExit as exc:
            # The examples use SystemExit to report bad input from the CLI.
            error = str(exc) or "The example exited."
        except Exception as exc:  # noqa: BLE001 — surfaced in the page
            error = f"{type(exc).__name__}: {exc}"
    return {
        "result": result,
        "html": console_html(console),
        "text": console_text(console),
        "error": error,
    }


def _render_output(
    output: dict[str, Any] | None,
    *,
    key: str,
    render: Callable[[Any], None] | None,
) -> None:
    """Show the last run's output, or a hint when there has not been one."""
    if output is None:
        st.caption("Press Run to execute the example and show its output here.")
        return

    if output["error"]:
        st.error(output["error"])

    if render is not None and output["result"] is not None:
        render(output["result"])
        with st.expander("Terminal output"):
            render_console_html(output["html"])
    else:
        render_console_html(output["html"])

    st.download_button(
        "Download output",
        data=output["text"],
        file_name=f"{key.replace('.', '_')}_output.txt",
        mime="text/plain",
        key=f"{key}.download",
    )
