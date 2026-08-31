"""Render an :mod:`argparse` parser as a Streamlit form.

The example scripts under ``examples/`` are the project's real entry points, and
each one already describes its own arguments — names, types, defaults, and help
text — to :class:`argparse.ArgumentParser`. This module turns that description
into widgets so a page never restates it. Add a flag to an example and its form
grows a control; change the help text and the tooltip follows.

:func:`render_form` returns the same :class:`argparse.Namespace` the script's
``run()`` would receive from the command line, and :func:`command_line` renders
the equivalent invocation so the page can show what it would have typed.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any, Callable

import streamlit as st


# A per-argument escape hatch: ``{dest: widget}``, where the widget is called
# with the action and a session-state key prefix and returns the value. Used for
# arguments a generic text box serves badly (a bundle directory, say).
WidgetOverride = Callable[[argparse.Action, str], Any]

_IGNORED_ACTIONS = (argparse._HelpAction, argparse._VersionAction)

# Words a plain capitalize() would mangle in a label ("Llm reader").
_ACRONYMS = {"llm", "csv", "tsv", "api", "url", "id", "json", "yaml", "xml", "sql"}


def render_form(
    parser: argparse.ArgumentParser,
    *,
    key_prefix: str,
    overrides: dict[str, WidgetOverride] | None = None,
) -> argparse.Namespace:
    """Render one widget per parser argument and collect the results.

    Args:
        parser: The example's parser, from its ``build_parser()``.
        key_prefix: Namespace for the widgets' session-state keys, so two pages
            showing similar arguments do not collide.
        overrides: Optional per-``dest`` widget replacements.

    Returns:
        A namespace holding a value for every argument the parser defines.
    """
    overrides = overrides or {}
    values: dict[str, Any] = {}

    for action in parser._actions:
        if isinstance(action, _IGNORED_ACTIONS):
            continue
        key = f"{key_prefix}.{action.dest}"
        override = overrides.get(action.dest)
        values[action.dest] = (
            override(action, key) if override else _render_action(action, key)
        )
    return argparse.Namespace(**values)


def command_line(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    script: str,
) -> str:
    """Render the command line equivalent to ``args``.

    Only values that differ from the parser's defaults are included, so the
    result reads like the command someone would actually type.

    Args:
        parser: The parser the arguments came from.
        args: The collected argument values.
        script: Path of the script, e.g. ``examples/resolve_catalog.py``.

    Returns:
        A single shell command string.
    """
    parts = ["python", script]
    for action in parser._actions:
        if isinstance(action, _IGNORED_ACTIONS):
            continue
        value = getattr(args, action.dest, None)
        if value is None or value == action.default:
            continue
        flag = _flag(action)
        if _is_flag_action(action):
            if value:
                parts.append(flag)
        elif isinstance(value, list):
            for item in value:
                parts.extend([flag, _render_value(item)])
        elif not action.option_strings:
            parts.append(_render_value(value))
        else:
            parts.extend([flag, _render_value(value)])
    return " ".join(parts)


def _render_value(value: Any) -> str:
    """Quote a value for the command line, shortening paths under the cwd.

    Streamlit runs from the repository root, so a bundle chosen in the UI reads
    as the relative path someone would actually type.
    """
    if isinstance(value, Path):
        try:
            value = value.relative_to(Path.cwd())
        except ValueError:
            pass
    return shlex.quote(str(value))


def _render_action(action: argparse.Action, key: str) -> Any:
    """Render the widget that matches ``action``'s kind and return its value."""
    label = _label(action)
    help_text = action.help or None

    if _is_flag_action(action):
        return st.checkbox(
            label, value=bool(action.default), help=help_text, key=key
        )

    if isinstance(action, argparse._CountAction):
        return int(
            st.number_input(
                label, min_value=0, value=int(action.default or 0),
                step=1, help=help_text, key=key,
            )
        )

    if isinstance(action, argparse._AppendAction) or action.nargs in ("*", "+"):
        return _render_multi(action, key, label, help_text)

    if action.choices:
        options = list(action.choices)
        index = options.index(action.default) if action.default in options else 0
        return st.selectbox(label, options, index=index, help=help_text, key=key)

    return _render_scalar(action, key, label, help_text)


def _render_multi(
    action: argparse.Action, key: str, label: str, help_text: str | None
) -> list[Any]:
    """Render a repeatable argument as one value per line."""
    default = action.default or []
    raw = st.text_area(
        f"{label} (one per line)",
        value="\n".join(str(item) for item in default),
        height=80,
        help=help_text,
        key=key,
    )
    items = [line.strip() for line in raw.splitlines() if line.strip()]
    return [_coerce(action, item) for item in items]


def _render_scalar(
    action: argparse.Action, key: str, label: str, help_text: str | None
) -> Any:
    """Render a single-value argument, honouring its declared ``type``."""
    if action.type in (int, float):
        value = st.number_input(
            label,
            value=action.type(action.default if action.default is not None else 0),
            help=help_text,
            key=key,
        )
        return action.type(value)

    raw = st.text_input(
        label,
        value="" if action.default is None else str(action.default),
        help=help_text,
        key=key,
    ).strip()
    if not raw:
        return action.default
    return _coerce(action, raw)


def _coerce(action: argparse.Action, raw: str) -> Any:
    """Apply the action's ``type`` callable, as argparse would."""
    if action.type is None:
        return raw
    try:
        return action.type(raw)
    except (TypeError, ValueError) as exc:
        raise st.StreamlitAPIException(
            f"{_flag(action)}: cannot read {raw!r} ({exc})"
        ) from exc


def _is_flag_action(action: argparse.Action) -> bool:
    """True for ``store_true`` / ``store_false`` arguments."""
    return isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))


def _flag(action: argparse.Action) -> str:
    """The argument's longest option string, or its dest when positional."""
    if action.option_strings:
        return max(action.option_strings, key=len)
    return action.dest


def _label(action: argparse.Action) -> str:
    """A readable widget label derived from the argument's name."""
    words = _flag(action).lstrip("-").replace("_", "-").split("-")
    spelled = [
        word.upper() if word.lower() in _ACRONYMS else word.lower() for word in words
    ]
    label = " ".join(spelled)
    return label if spelled[0].isupper() else label.capitalize()
