"""``python -m eval sheet`` to create a labeling sheet; ``python -m eval score`` to grade.

Two subcommands because they are used at different rates: ``sheet`` runs once per
bundle, ``score`` runs every time the router changes. Both share the bundle, standard
and judge flags, so a scored run names exactly the configuration that produced it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from eval.labels import (
    ALTERNATIVE_SEP,
    UNANSWERABLE,
    load_evidence,
    load_labels,
    score,
)
from eval.score import report
from eval.sheet import write_sheet, write_sources
from src.cli.options import JUDGE_BACKING, add_judge_options, add_model_options, judges_from_args
from src.pipelines.catalog import DEFAULT_BUNDLE, resolve
from src.pipelines.models import JUDGE_MODULE
from src.pipelines.routing import DEFAULT_STANDARD, route
from src.context import create_context
from src.router.bundle import NONE, discover_bundle, select
from src.router.route import _passage_reader
from src.standards import METADATA_STANDARDS

DATA = Path(__file__).resolve().parent / "data"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m eval",
        description="Create or grade router ground truth.",
    )
    ap.add_argument("command", choices=("sheet", "score"),
                    help="sheet: write a labeling sheet (once per bundle). "
                         "score: grade the current router against a filled-in one")

    source = ap.add_argument_group("Input", "The bundle whose routing is labeled.")
    source.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    source.add_argument("--dictionary", action="append", default=None,
                        help=f"codebooks by filename (repeatable); '{NONE}' for none")
    source.add_argument("--doc", action="append", default=None,
                        help=f"documents by filename (repeatable); '{NONE}' for none")
    source.add_argument("--standard", default=DEFAULT_STANDARD,
                        choices=sorted(METADATA_STANDARDS))
    source.add_argument("--candidates", type=int, default=5,
                        help="candidates per field — the recall budget the sheet measures")
    source.add_argument("--out", type=Path, default=None,
                        help="sheet directory (default: eval/data/<standard>__<bundle>)")

    router = ap.add_argument_group("Router", "The configuration being graded.")
    router.add_argument("--llm-candidate-judge", action="store_true",
                        help="decide each field with the LLM judges (tool matcher, column "
                             "matcher, passage reader)")

    model = ap.add_argument_group("LLM candidate judge model", "Backing --llm-candidate-judge.")
    add_model_options(model, JUDGE_MODULE, backing=JUDGE_BACKING)
    add_judge_options(model)
    return ap


def run(args: argparse.Namespace, console: Console) -> Path:
    bundle = discover_bundle(args.bundle)
    judges = judges_from_args(args)
    resolved = resolve(
        bundle,
        select(bundle.codebooks, args.dictionary),
        select(bundle.documents, args.doc),
    )
    field_plan = route(
        resolved, args.standard, candidates=args.candidates, judges=judges
    )

    out = args.out or (DATA / f"{args.standard}__{args.bundle.name}")
    sheet = out / "labels.csv"

    if args.command == "score":
        console.print(
            f"[dim]candidate judge: {judges.label}[/]"
        )
        # The passage reader is what lets a document label be graded at the passage
        # rather than at the file; without it the sheet's `evidence` column is inert.
        passage = _passage_reader(
            [create_context(str(p), name=p.stem) for p in resolved.documents]
        )
        evidence = load_evidence(sheet)
        report(
            score(field_plan, load_labels(sheet), evidence, passage),
            console,
            args.candidates,
            evidence,
            judged=field_plan.judged,
        )
        return sheet

    written, fresh = write_sheet(field_plan, args.standard, sheet, args.candidates)
    sources = write_sources(resolved.catalog, field_plan, out / "sources.csv")
    if not fresh:
        console.print(
            f"[yellow]{sheet.name} already has labels — wrote {written.name} instead "
            "so your work is not overwritten.[/]"
        )
    console.print(f"[bold]sheet:[/]   {written}  ({len(field_plan.routings)} fields)")
    console.print(f"[bold]sources:[/] {sources}")
    console.print(
        f"\nFill the [bold]answer[/] column with a [bold]ref[/] from sources.csv "
        f"(several allowed, separated by '{ALTERNATIVE_SEP}'), or [bold]{UNANSWERABLE}[/] "
        "if nothing in this bundle answers the field. Look past the rank columns — an "
        "answer the router never retrieved is exactly what this measures.\n"
        "Then: [bold]python -m eval score[/]"
    )
    return written


def main() -> None:
    run(build_parser().parse_args(), Console())


if __name__ == "__main__":
    main()
