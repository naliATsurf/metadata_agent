"""Example: resolve a bundle's columns into a described catalog, and show the work.

Catalog resolution (layer 3) turns opaque column names into *described* columns by
harvesting meanings from the other files in the bundle — a codebook, a README —
and cross-checking each borrowed claim against the column's actual values. This is
standard-agnostic: it describes the data, independent of any metadata schema.

This example resolves a bundle and prints, per column: the resolved meaning, *how*
it was resolved (structured dictionary > prose > value prior), the confidence, the
citation, and any conflicts or corroboration surfaced along the way.

Usage:

    # default: the router_test bundle (observations + a codebook with a Kelvin trap)
    python examples/resolve_catalog.py

    # any bundle; mark codebook CSVs as dictionaries (sources, not data tables)
    python examples/resolve_catalog.py --bundle data/sample/sharetrait_preprocessed/TRADAT031
    python examples/resolve_catalog.py --bundle mydir --dictionary variables.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich import box
from rich.console import Console
from rich.table import Table

from src.context import create_context
from src.router import Catalog, resolve_bundle, resolve_catalog

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"

_CONF_STYLE = {"high": "green", "medium": "yellow", "low": "red", "none": "dim"}
_METHOD_ABBR = {
    "structured_dictionary": "dictionary",
    "lexical_prose": "prose",
    "value_prior": "value",
    "none": "none",
}


def discover(bundle: Path, dictionaries: List[str]) -> Tuple[List[Path], List[Path], List[Path]]:
    """Partition a bundle into data tables, dictionary CSVs, and documents."""
    csvs = sorted(bundle.glob("*.csv"))
    docs = sorted([*bundle.glob("*.md"), *bundle.glob("*.txt")])
    if not csvs:
        raise SystemExit(f"No CSV found in {bundle}.")
    dict_names = set(dictionaries or [])
    tables = [c for c in csvs if c.name not in dict_names]
    dicts = [c for c in csvs if c.name in dict_names]
    if not tables:
        raise SystemExit("Every CSV was marked --dictionary; at least one data table is needed.")
    return tables, dicts, docs


def resolve(tables: List[Path], dicts: List[Path], docs: List[Path]) -> Catalog:
    """Resolve one or many data tables against the codebooks / documents."""
    table_ctx = [create_context(str(p), name=p.stem) for p in tables]
    sources = [create_context(str(p), name=p.stem) for p in (*dicts, *docs)]
    if len(table_ctx) == 1:
        return resolve_catalog(table_ctx[0], sources=sources)
    return resolve_bundle(table_ctx, sources=sources)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def print_overview(console: Console, catalog: Catalog) -> None:
    table = Table(
        title="Resolved catalog", box=box.SIMPLE_HEAVY, header_style="bold",
        title_justify="left", expand=True,
    )
    table.add_column("Table", style="dim", no_wrap=True)
    table.add_column("Column", style="cyan bold", no_wrap=True)
    table.add_column("Prior", no_wrap=True)
    table.add_column("Method", no_wrap=True)
    table.add_column("Conf", no_wrap=True)
    table.add_column("Meaning (and citation)", ratio=1)

    for c in catalog.columns:
        conf = f"[{_CONF_STYLE.get(c.link_confidence, 'white')}]{c.link_confidence}[/]"
        meaning = c.description or "[dim](unresolved — abstained)[/]"
        if c.units:
            meaning += f"  [dim]\\[{c.units}][/]"
        if c.link_evidence:
            meaning += f"  [dim]← {c.link_evidence}[/]"
        table.add_row(
            c.resource, c.name, c.value_label or "-",
            _METHOD_ABBR.get(c.link_method, c.link_method), conf, meaning,
        )
    console.print(table)


def print_conflicts_and_corroboration(console: Console, catalog: Catalog) -> None:
    interesting = [
        c for c in catalog.columns if c.conflicts or c.corroborated_by or c.alternatives
    ]
    if not interesting:
        return
    console.print("\n[bold]Conflicts, corroboration, and alternatives[/]")
    for c in interesting:
        console.print(f"\n[cyan bold]{c.resource}.{c.name}[/] — {c.description or '(unresolved)'}")
        for msg in c.conflicts:
            console.print(f"  [red]✗ conflict:[/] {msg}")
        for cite in c.corroborated_by:
            console.print(f"  [green]✓ corroborated by:[/] {cite}")
        for alt in c.alternatives:
            desc = alt.get("description") or alt.get("units") or "?"
            console.print(
                f"  [dim]· alternative ({alt.get('method')}, {alt.get('confidence')}): "
                f"{desc} — {alt.get('evidence')}[/]"
            )


def print_summary(console: Console, catalog: Catalog) -> None:
    methods = Counter(c.link_method for c in catalog.columns)
    confs = Counter(c.link_confidence for c in catalog.columns)
    resolved = sum(1 for c in catalog.columns if c.link_method != "none")
    console.print(
        f"\n[bold]{resolved}/{len(catalog.columns)}[/] columns resolved   "
        f"methods={dict(methods)}   confidence={dict(confs)}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolve a bundle's columns and show the evidence.")
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="bundle directory")
    ap.add_argument("--dictionary", action="append", default=["codebook.csv"],
                    help="a codebook CSV to treat as a source, not a data table (repeatable)")
    args = ap.parse_args()

    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(f"Bundle {args.bundle} is missing or empty.")

    tables, dicts, docs = discover(args.bundle, args.dictionary)
    console = Console()
    console.print(f"[bold]bundle:[/] {args.bundle}")
    console.print(f"tables: {[p.name for p in tables]}   "
                  f"dictionaries: {[p.name for p in dicts] or 'none'}   "
                  f"docs: {[p.name for p in docs] or 'none'}\n")

    catalog = resolve(tables, dicts, docs)
    print_overview(console, catalog)
    print_conflicts_and_corroboration(console, catalog)
    print_summary(console, catalog)


if __name__ == "__main__":
    main()
