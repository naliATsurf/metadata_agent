"""The command line that ships with the library: ``metadata-agent <command>``.

One command per stage of a pipeline, because the stages are run and inspected
separately — resolve a bundle once, then route it as many times as you like:

===========  =========================================================
``resolve``  a bundle's columns → a described catalog (layer 3)
``route``    a catalog → a field plan and the plan to execute (4 and 5)
``generate`` the agentic pipeline: plan, execute, write the record
===========  =========================================================

Each command is a module with its own ``build_parser`` and ``run``, so a front end can
enumerate a command's flags to build a form and call the same code the terminal does
(``demo/``), and a harness can drive it without argparse at all (``eval/``). The
pipelines themselves are in :mod:`src.pipelines`; nothing here decides anything, it
parses, prints and saves.

Run it as ``metadata-agent resolve …`` once installed, or ``python -m src.cli resolve …``
from a checkout.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from src.cli import generate as generate_command
from src.cli import resolve as resolve_command
from src.cli import route as route_command

COMMANDS = {
    "resolve": resolve_command,
    "route": route_command,
    "generate": generate_command,
}


def usage() -> str:
    lines = ["usage: metadata-agent <command> [options]", "", "commands:"]
    lines += [
        f"  {name:<9} {module.__doc__.splitlines()[0].split('—', 1)[-1].strip()}"
        for name, module in COMMANDS.items()
    ]
    lines += ["", "`metadata-agent <command> --help` for a command's options."]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Dispatch to a command, or print the usage and exit 2."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in COMMANDS:
        print(usage())
        raise SystemExit(0 if arguments[:1] in (["-h"], ["--help"]) else 2)
    COMMANDS[arguments[0]].main(arguments[1:])
