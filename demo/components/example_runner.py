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
    Defaults,
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
from src.llm_calls import count_llm_calls


def run_example(
    module: ModuleType,
    *,
    key: str,
    script: str,
    title: str,
    intro: str | None = None,
    overrides: dict[str, WidgetOverride] | None = None,
    defaults: Defaults | None = None,
    columns: int = 2,
    render: Callable[[Any, int], None] | None = None,
    inputs: dict[str, Any] | None = None,
    preceding_command: str | None = None,
    layout: list[list[str]] | None = None,
    enabled_by: dict[str, str] | None = None,
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
        defaults: Starting values for arguments the app already has an answer
            for, replacing the parser's own defaults.
        columns: How many columns to lay the argument widgets out in.
        render: Optional renderer for whatever ``run()`` returned, called with that
            result and the number of LLM calls the run made. Given one,
            the page shows it and keeps the printed output as a fallback;
            without one, the printed output is all there is to show.
        inputs: Values handed to ``run()`` as keyword arguments rather than parsed
            from the form — what an earlier page produced, passed on in memory.
        preceding_command: The command that produced those inputs, shown before
            this page's own so the displayed commands still reproduce the run.
        layout: Where the argument groups go: one list of group titles per column,
            stacked top to bottom. Without it groups fill rows in declaration order.
        enabled_by: Arguments that only mean something with a flag set, keyed by
            group title or by argument ``dest``, valued by the flag's ``dest``.
            They are shown greyed out while the flag is off.
    """
    parser = module.build_parser()

    st.title(title)
    st.caption(intro or parser.description or "")

    args = _render_arguments(
        parser, key=key, overrides=overrides, defaults=defaults, columns=columns,
        layout=layout, disabled=_disabled_by(parser, key, defaults, enabled_by or {}),
    )

    command = command_line(parser, args, script=script)
    shown = f"{preceding_command}\n{command}" if preceding_command else command
    st.code(shown, language="bash")
    if defaults is not None and defaults.note:
        st.caption(defaults.note)

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
        st.session_state[f"{key}.output"] = {
            **_execute(module, args, width, inputs or {}),
            "command": command,
        }

    _render_output(st.session_state.get(f"{key}.output"), key=key, render=render)


#: Most groups to put side by side before wrapping to another row. Past this the
#: columns are too narrow for a label and its help icon to share a line.
_GROUPS_PER_ROW = 3


def _disabled_by(
    parser: argparse.ArgumentParser,
    key: str,
    defaults: Defaults | None,
    enabled_by: dict[str, str],
) -> Callable[[argparse.Action], bool] | None:
    """A predicate greying out the arguments whose enabling flag is off.

    The flag is read from session state rather than from the form's collected values,
    so the answer does not depend on whether the flag's widget happens to be drawn
    before the arguments it controls. Before the first interaction the widget has no
    state yet, and the flag's starting value applies.
    """
    if not enabled_by:
        return None
    defaults = defaults or Defaults()
    actions = {action.dest: action for action in parser._actions}
    group_of = {
        action.dest: group.title
        for group in _argument_groups(parser)
        for action in group._group_actions
    }

    def flag_on(dest: str) -> bool:
        state = st.session_state.get(defaults.key(key, dest))
        return bool(defaults.default_for(actions[dest]) if state is None else state)

    def disabled(action: argparse.Action) -> bool:
        flag = enabled_by.get(action.dest) or enabled_by.get(group_of.get(action.dest))
        return flag is not None and not flag_on(flag)

    return disabled


def _render_arguments(
    parser: argparse.ArgumentParser,
    *,
    key: str,
    overrides: dict[str, WidgetOverride] | None,
    defaults: Defaults | None,
    columns: int,
    layout: list[list[str]] | None = None,
    disabled: Callable[[argparse.Action], bool] | None = None,
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

    if layout is not None:
        by_title = {group.title: (group, actions) for group, actions in populated}
        placed = [title for column in layout for title in column]
        missing = set(by_title) - set(placed)
        unknown = set(placed) - set(by_title)
        if missing or unknown:
            raise ValueError(
                f"layout must place every argument group exactly by title; "
                f"missing {sorted(missing)}, unknown {sorted(unknown)}"
            )
        for titles, column in zip(layout, st.columns(len(layout), gap="medium")):
            with column:
                for title in titles:
                    group, actions = by_title[title]
                    with st.container(border=True):
                        _render_group_heading(group)
                        values.update(vars(
                            _render_actions(actions, key, overrides, defaults, 1, disabled)
                        ))
        return argparse.Namespace(**values)

    # Groups side by side, each one's arguments stacked under its title: the form
    # stays short instead of scrolling. Rows of at most `_GROUPS_PER_ROW`, because a
    # parser with many groups would otherwise squeeze them all into one row. A parser
    # with no groups of its own falls back to spreading its arguments across `columns`.
    if len(populated) > 1:
        for row in _rows(populated, _GROUPS_PER_ROW):
            for (group, actions), column in zip(row, st.columns(len(row), gap="medium")):
                with column, st.container(border=True):
                    _render_group_heading(group)
                    values.update(
                        vars(_render_actions(actions, key, overrides, defaults, 1, disabled))
                    )
        return argparse.Namespace(**values)

    for group, actions in populated:
        with st.container(border=True):
            _render_group_heading(group)
            values.update(
                vars(_render_actions(actions, key, overrides, defaults, columns, disabled))
            )
    return argparse.Namespace(**values)


def _rows(items: list[Any], per_row: int) -> list[list[Any]]:
    """Split ``items`` into rows of at most ``per_row``, keeping their order.

    The last row is balanced against the one before it, so four groups lay out as
    2 + 2 rather than 3 + 1 and no section ends up alone at full width.
    """
    if len(items) <= per_row:
        return [items]
    rows = -(-len(items) // per_row)
    width = -(-len(items) // rows)
    return [items[start:start + width] for start in range(0, len(items), width)]


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
    defaults: Defaults | None,
    columns: int,
    disabled: Callable[[argparse.Action], bool] | None = None,
) -> argparse.Namespace:
    """Lay one group's arguments out across ``columns`` and collect their values."""
    if columns <= 1 or len(actions) == 1:
        return render_form(
            _parser_over(actions), key_prefix=key, overrides=overrides,
            defaults=defaults, disabled=disabled,
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
                _parser_over(subset), key_prefix=key, overrides=overrides,
                defaults=defaults, disabled=disabled,
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


def _execute(
    module: ModuleType, args: argparse.Namespace, width: int, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Run the example, capturing its console output and any failure."""
    console = recording_console(width=width)
    error: str | None = None
    result: Any = None
    with st.spinner("Running…"), count_llm_calls() as llm_calls:
        try:
            result = module.run(args, console, **inputs)
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
        "llm_calls": llm_calls.calls,
    }


def _render_output(
    output: dict[str, Any] | None,
    *,
    key: str,
    render: Callable[[Any, int], None] | None,
) -> None:
    """Show the last run's output, or a hint when there has not been one."""
    if output is None:
        st.caption("Press Run to execute the example and show its output here.")
        return

    if output["error"]:
        st.error(output["error"])

    if render is not None and output["result"] is not None:
        render(output["result"], output.get("llm_calls", 0))
        with st.expander("Terminal output"):
            render_console_html(output["html"])
    else:
        # No renderer to put the count among its tallies, so it goes above the output.
        if output.get("llm_calls"):
            st.metric("LLM calls", output["llm_calls"])
        render_console_html(output["html"])

    st.download_button(
        "Download output",
        data=output["text"],
        file_name=f"{key.replace('.', '_')}_output.txt",
        mime="text/plain",
        key=f"{key}.download",
    )
