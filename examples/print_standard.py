"""Example: pretty-print a metadata standard, given its name.

Renders a standard's fields as a table — name, type, whether it is required, and
description — using ``rich``. With no name, it lists the available standards.

Usage:

    python examples/print_standard.py sharetrait_basic
    python examples/print_standard.py field_router_test --hints
    python examples/print_standard.py            # list available standards
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich import box
from rich.console import Console
from rich.table import Table

from src.standards import STANDARD_DEFINITIONS, _format_type_name


def _base_type(spec: dict) -> str:
    """The type name with any ``Optional[...]`` wrapper stripped (optionality is its own column)."""
    name = _format_type_name(spec["type"])
    if name.startswith("Optional[") and name.endswith("]"):
        return name[len("Optional["):-1]
    return name


def print_standard(name: str, show_hints: bool = False) -> None:
    spec = STANDARD_DEFINITIONS.get(name)
    console = Console()
    if spec is None:
        console.print(f"[red]Unknown standard[/] [bold]{name}[/].")
        _list_standards(console)
        raise SystemExit(1)

    n_required = sum(1 for s in spec.values() if s["default"] is ...)
    table = Table(
        title=f"[bold]{name}[/]  —  {len(spec)} fields, {n_required} required",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        title_justify="left",
        expand=True,
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Field", style="cyan bold", no_wrap=True)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Req", justify="center", no_wrap=True)
    table.add_column("Description", ratio=1)
    if show_hints:
        table.add_column("Prompt hint", ratio=1, style="dim italic")

    for i, (field, s) in enumerate(spec.items(), 1):
        required = s["default"] is ...
        req_cell = "[green]●[/]" if required else "[dim]○[/]"
        row = [str(i), field, _base_type(s), req_cell, s["description"]]
        if show_hints:
            row.append(s.get("prompt_hint", ""))
        table.add_row(*row)

    console.print(table)
    console.print(
        "[dim]● required   ○ optional[/]",
        justify="left",
    )


def _list_standards(console: Console) -> None:
    console.print("\n[bold]Available standards:[/]")
    for name in sorted(STANDARD_DEFINITIONS):
        spec = STANDARD_DEFINITIONS[name]
        n_req = sum(1 for s in spec.values() if s["default"] is ...)
        console.print(f"  [cyan]{name}[/]  [dim]({len(spec)} fields, {n_req} required)[/]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Pretty-print a metadata standard.")
    ap.add_argument("name", nargs="?", help="the standard name (omit to list all)")
    ap.add_argument("--hints", action="store_true", help="also show each field's prompt hint")
    args = ap.parse_args()

    if not args.name:
        _list_standards(Console())
        return
    print_standard(args.name, show_hints=args.hints)


if __name__ == "__main__":
    main()
