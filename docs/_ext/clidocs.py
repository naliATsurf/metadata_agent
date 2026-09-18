"""Sphinx extension: generate the CLI reference from the live parsers.

Every command in :mod:`src.cli` exposes ``build_parser()``, so the reference is read off
the same objects the terminal parses with — usage line, option groups, defaults, choices,
help. A flag added to a command documents itself; a flag renamed cannot leave a stale
entry behind, which is what a hand-written reference always ends up doing.

The page is written at build time to ``docs/cli-reference.md`` and is not checked in.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1]
ROOT = DOCS.parent
OUTPUT = DOCS / "cli-reference.md"

#: Where each command's defaults come from when a flag is left alone.
DEFAULT_NOTES = {
    "provider": "`LLM_PROVIDER_<MODULE>`, else `LLM_PROVIDER`",
    "model": "`LLM_MODEL_<MODULE>`, else `LLM_MODEL`",
    "temperature": "`LLM_TEMPERATURE_<MODULE>`",
}


def _flags(action: argparse.Action) -> str:
    """How the option is spelled, with its metavar — ``--bundle BUNDLE``."""
    if not action.option_strings:
        return f"`{action.metavar or action.dest}`"
    spelling = ", ".join(f"`{option}`" for option in action.option_strings)
    if action.nargs == 0 or isinstance(action, argparse._StoreConstAction):
        return spelling
    metavar = action.metavar or action.dest.upper()
    return f"{spelling} `{metavar}`"


def _default(action: argparse.Action) -> str:
    """What happens when the flag is left out."""
    if action.dest in DEFAULT_NOTES:
        return DEFAULT_NOTES[action.dest]
    if action.required:
        return "**required**"
    if action.default in (None, False, [], ()):
        return "—"
    if isinstance(action.default, Path):
        try:
            return f"`{action.default.relative_to(ROOT)}`"
        except ValueError:
            return f"`{action.default}`"
    return f"`{action.default}`"


def _notes(action: argparse.Action) -> str:
    """What the reader cannot see from the flag alone: repeatable, choices, type."""
    notes = []
    if isinstance(action, argparse._AppendAction):
        notes.append("repeatable")
    if action.choices:
        notes.append("one of " + ", ".join(f"`{choice}`" for choice in sorted(map(str, action.choices))))
    elif action.nargs == 0 or isinstance(action, argparse._StoreConstAction):
        notes.append("flag")
    elif action.type is not None:
        notes.append(getattr(action.type, "__name__", str(action.type)))
    return " · ".join(notes)


def _group_sections(parser: argparse.ArgumentParser) -> list[str]:
    """One table per option group, in the order the command declares them."""
    out: list[str] = []
    for group in parser._action_groups:
        actions = [a for a in group._group_actions if not isinstance(a, argparse._HelpAction)]
        if not actions:
            continue
        title = group.title if group.title[:1].isupper() else group.title.capitalize()
        out += [f"#### {title}", ""]
        if group.description:
            out += [group.description, ""]
        out += ["| Option | Default | Notes | Does |", "|---|---|---|---|"]
        for action in actions:
            # The Default column already says where a default comes from, so drop the
            # "(default: …)" argparse help repeats — it is machine-specific anyway.
            help_text = " ".join((action.help or "").split()).replace("|", "\\|")
            help_text = re.sub(r"\s*\(default:[^)]*\)", "", help_text)
            out.append(
                f"| {_flags(action)} | {_default(action)} | {_notes(action) or '—'} | {help_text} |"
            )
        out.append("")
    return out


def _summary(module) -> str:
    """The command's one-line purpose, from its module docstring."""
    line = (module.__doc__ or "").splitlines()[0].split("—", 1)[-1].strip()
    return line[:1].upper() + line[1:]


def _command_section(name: str, module) -> list[str]:
    parser = module.build_parser()
    summary = _summary(module)
    usage = " ".join(parser.format_usage().replace("usage: ", "").split())
    return [
        f"### `{name}`",
        "",
        summary,
        "",
        "```text",
        usage,
        "```",
        "",
        *_group_sections(parser),
    ]


def generate(app=None):
    """Write ``docs/cli-reference.md`` from every command's parser."""
    from src.cli import COMMANDS

    page = [
        "# CLI reference",
        "",
        "*Generated at build time from the parsers in `src/cli/`. Do not edit this page "
        "by hand — edit the command and rebuild.*",
        "",
        "```text",
        "metadata-agent <command> [options]",
        "```",
        "",
        "Run it as `metadata-agent …` once installed (`uv pip install -e .`), or "
        "`python -m src.cli …` from a checkout. For what the commands are *for*, see the "
        "[command line tutorial](tutorials/cli.md).",
        "",
        "| Command | Does |",
        "|---|---|",
    ]
    page += [f"| [`{name}`](#{name}) | {_summary(module)} |" for name, module in COMMANDS.items()]
    page.append("")
    page.append("## Commands")
    page.append("")
    for name, module in COMMANDS.items():
        page += _command_section(name, module)

    page += [
        "## Where a default comes from",
        "",
        "The model options default to the configuration of the module behind that "
        "command — `LLM_PROVIDER_CATALOG_RESOLVER` for `resolve`, "
        "`LLM_PROVIDER_CANDIDATE_JUDGE` for `route` — falling back to the global "
        "`LLM_PROVIDER` / `LLM_MODEL`. So `--help` and the app's form show what a run "
        "would actually use, and an override is visibly an override.",
        "",
        "Numbers the pipeline decides by (passage sizes, batch sizes, sampling) are not "
        "flags: they are thresholds, settable as `THRESHOLD_<NAME>` in `.env` or in the "
        "app's settings panel. Every one is listed in `src/thresholds.py`.",
        "",
    ]
    OUTPUT.write_text("\n".join(page))


def setup(app):
    app.connect("builder-inited", generate)
    return {"parallel_read_safe": True, "version": "0.1"}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    generate()
    print(f"wrote {OUTPUT}")
