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

    # add the retrieve-then-read prose tier (localizes a definition in a long /
    # multi-file document, then reads a cued meaning) on top of the glossary regex
    python examples/resolve_catalog.py --prose-reader
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console

from src.context import create_context
from src.router import (
    Catalog,
    DeterministicProseReader,
    ProseReader,
    render_catalog,
    resolve_bundle,
    resolve_catalog,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"


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


def resolve(
    tables: List[Path],
    dicts: List[Path],
    docs: List[Path],
    prose_reader: ProseReader | None = None,
) -> Catalog:
    """Resolve one or many data tables against the codebooks / documents."""
    table_ctx = [create_context(str(p), name=p.stem) for p in tables]
    sources = [create_context(str(p), name=p.stem) for p in (*dicts, *docs)]
    if len(table_ctx) == 1:
        return resolve_catalog(table_ctx[0], sources=sources, prose_reader=prose_reader)
    return resolve_bundle(table_ctx, sources=sources, prose_reader=prose_reader)


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolve a bundle's columns and show the evidence.")
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="bundle directory")
    ap.add_argument("--dictionary", action="append", default=["codebook.csv"],
                    help="a codebook CSV to treat as a source, not a data table (repeatable)")
    ap.add_argument("--prose-reader", action="store_true",
                    help="enable the retrieve-then-read prose tier (localize + read a "
                         "cued definition) above the glossary regex")
    args = ap.parse_args()

    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(f"Bundle {args.bundle} is missing or empty.")

    tables, dicts, docs = discover(args.bundle, args.dictionary)
    reader = DeterministicProseReader() if args.prose_reader else None
    console = Console()
    console.print(f"[bold]bundle:[/] {args.bundle}")
    console.print(f"tables: {[p.name for p in tables]}   "
                  f"dictionaries: {[p.name for p in dicts] or 'none'}   "
                  f"docs: {[p.name for p in docs] or 'none'}   "
                  f"prose-reader: {'on' if reader else 'off'}\n")

    catalog = resolve(tables, dicts, docs, prose_reader=reader)
    render_catalog(catalog, console)


if __name__ == "__main__":
    main()
