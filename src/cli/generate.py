"""``generate`` — run the whole agentic pipeline over a dataset (plan → execute → record).

The other commands are stages of the field-driven path; this one drives the orchestrator,
which plans, runs players with tools, and writes the record. It still lives in
:mod:`src.main` — this exposes it as a command, and its parser so the CLI reference can
document it.

Usage::

    metadata-agent generate --source data/my_data.csv --topology default
"""

from __future__ import annotations

import argparse
from typing import Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    from src.main import build_parser as parser_of

    return parser_of()


def main(argv: Optional[Sequence[str]] = None) -> None:
    from src.main import main as run_generation

    run_generation(list(argv) if argv is not None else None)
