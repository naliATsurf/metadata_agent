"""``resolve`` — describe a bundle's columns, and show the evidence (layer 3).

Catalog resolution turns opaque column names into *described* columns by harvesting
meanings from the other files in the bundle — a codebook, a README — and cross-checking
each borrowed claim against the column's actual values. It is standard-agnostic: it
describes the data, independent of any metadata schema.

Usage::

    metadata-agent resolve                                  # the sample bundle
    metadata-agent resolve --bundle mydir --dictionary variables.csv
    metadata-agent resolve --doc readme_hard.txt --llm-reader
    metadata-agent resolve --llm-reader --debug             # log prompts and replies
    metadata-agent resolve --out catalog.json               # save, to route later
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from src.cli.options import CATALOG_BACKING, add_model_options, readers_from_args
from src.pipelines.catalog import DEFAULT_BUNDLE, resolve
from src.pipelines.models import CATALOG_MODULE
from src.router import NONE, ResolvedBundle, discover_bundle, render_catalog, select


def build_parser() -> argparse.ArgumentParser:
    """The command's argument surface, built separately so a UI can render it.

    Keeping the parser out of :func:`main` lets a front end enumerate the flags and
    their help text to build a form, instead of duplicating them.
    """
    ap = argparse.ArgumentParser(
        prog="metadata-agent resolve",
        description="Resolve a bundle's columns and show the evidence.",
    )
    source = ap.add_argument_group(
        "Input", "The bundle, and which of its discovered sources to resolve from."
    )
    tier = ap.add_argument_group(
        "Prose reader", "Whether a model reads the narrative no codebook covers."
    )
    model = ap.add_argument_group(
        "LLM prose reader model",
        "Backing --llm-reader; each defaults to that module's configuration.",
    )

    source.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE,
                        help="bundle directory")
    source.add_argument("--dictionary", action="append", default=None,
                        help="codebooks to use, by filename (repeatable). Omit to use "
                             f"every codebook found in the bundle; pass '{NONE}' to use none")
    tier.add_argument("--llm-reader", action="store_true",
                      help="enable the LLM prose reader (reads free narrative) using the "
                           "configured model, for columns no codebook covers; the same "
                           "model judges which differently worded claims agree")
    source.add_argument("--doc", action="append", default=None,
                        help="documents to use, by filename (repeatable). Omit to use "
                             f"every document found in the bundle; pass '{NONE}' to use none")
    tier.add_argument("--debug", action="store_true",
                      help="with --llm-reader, log each prompt and raw model response "
                           "(and surface an error the reader would otherwise swallow)")
    source.add_argument("--out", type=Path, default=None,
                        help="write the resolution as JSON, for `metadata-agent route "
                             "--catalog`")
    add_model_options(model, CATALOG_MODULE, backing=CATALOG_BACKING)
    return ap


def run(args: argparse.Namespace, console: Console) -> ResolvedBundle:
    """Resolve the bundle described by ``args`` and print the evidence to ``console``.

    The resolution is returned as well — the catalog and the files it came from — for
    callers that render it themselves or hand it on to the router.
    """
    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(f"Bundle {args.bundle} is missing or empty.")
    try:
        bundle = discover_bundle(args.bundle)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    dicts = select(bundle.codebooks, args.dictionary)
    docs = select(bundle.documents, args.doc)
    readers = readers_from_args(args, console)
    console.print(f"[bold]bundle:[/] {args.bundle}")
    console.print(f"tables: {[p.name for p in bundle.tables]}   "
                  f"dictionaries: {[p.name for p in dicts] or 'none'}   "
                  f"docs: {[p.name for p in docs] or 'none'}   "
                  f"reader: {readers.label}")
    excluded = [
        p.name for p in (*bundle.codebooks, *bundle.documents) if p not in (*dicts, *docs)
    ]
    if excluded:
        console.print(f"[dim]discovered but not used: {excluded}[/]")
    console.print("")

    resolved = resolve(bundle, dicts, docs, readers)
    render_catalog(resolved.catalog, console)
    return resolved


def main(argv: list[str] | None = None) -> None:
    args, console = build_parser().parse_args(argv), Console()
    resolved = run(args, console)
    # Written here, not in run(): producing a file is a command-line act, and a UI
    # driving run() hands the resolution on in memory.
    if args.out:
        console.print(f"\nWrote {resolved.save(args.out)}.")
