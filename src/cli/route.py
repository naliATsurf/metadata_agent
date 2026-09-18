"""``route`` — route a resolved catalog into a field-driven plan (layers 4 and 5).

It starts from a *resolution* — what ``metadata-agent resolve --out`` saved, the catalog
together with the bundle files it came from — rather than resolving again, so the two
stages are run, inspected and varied separately. A real repository is **many tables**:
the fields of one schema are answered by columns in different CSVs, so each field is
routed to whichever table's column (or document span) answers it, and the routing is
compiled into a plan whose extraction tasks are grouped per table.

Usage::

    metadata-agent resolve --out catalog.json
    metadata-agent route --catalog catalog.json
    metadata-agent route --catalog catalog.json --llm-candidate-judge --debug
    metadata-agent route --catalog catalog.json --search-doc readme_long.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from src.cli.display import print_plan, print_routing, print_working, write_artifacts
from src.cli.options import (
    JUDGE_BACKING,
    add_judge_options,
    add_model_options,
    judges_from_args,
)
from src.config import llm_settings
from src.pipelines.field_driven import FieldDrivenRun
from src.pipelines.models import JUDGE_MODULE
from src.pipelines.routing import DEFAULT_STANDARD, route_and_compile
from src.router import NONE, ResolvedBundle, select
from src.standards import METADATA_STANDARDS


def build_parser() -> argparse.ArgumentParser:
    """The command's argument surface, built separately so a UI can render it."""
    ap = argparse.ArgumentParser(
        prog="metadata-agent route",
        description="Route a resolved catalog into a field-driven plan.",
    )
    source = ap.add_argument_group(
        "Input", "The resolution to route: a catalog and the files it came from."
    )
    target = ap.add_argument_group(
        "Metadata standard", "The schema whose fields are routed."
    )
    routing = ap.add_argument_group(
        "Routing", "How many candidates the router keeps per field."
    )
    model = ap.add_argument_group(
        "LLM candidate judge model",
        "Backing --llm-candidate-judge; each defaults to that module's configuration.",
    )

    source.add_argument("--catalog", type=Path, required=True,
                        help="a resolution saved by `metadata-agent resolve --out`")
    source.add_argument("--search-doc", action="append", default=None,
                        help="search only these of the resolution's documents by "
                             f"filename (repeatable); '{NONE}' for none. Deliberately "
                             "not the resolver's --doc: that chose what *described the "
                             "columns*, this chooses what is *searched to answer* the "
                             "fields no column answers. Narrowing here re-routes; it "
                             "does not resolve again")
    target.add_argument("--standard", default=DEFAULT_STANDARD,
                        choices=sorted(METADATA_STANDARDS),
                        help="metadata standard whose fields are routed")
    routing.add_argument("--candidates", type=int, default=5,
                         help="how many ranked candidates to keep per field. The router "
                              "proposes a set and the executor picks from it, so this is "
                              "the recall budget, not a display setting")
    routing.add_argument("--debug", action="store_true",
                         help="show each field's working: what does not fit on type or "
                              "units, every candidate, and the verdict, quotes and "
                              "citations that came back. With --llm-candidate-judge it "
                              "also logs each prompt and raw response, and surfaces an "
                              "error the judges would otherwise turn into a silent "
                              "abstention")
    routing.add_argument("--llm-candidate-judge", action="store_true",
                         help="let an LLM decide what answers each field, or that nothing "
                              "does: a tool matcher over the tools (answers cached in "
                              ".cache/tool_matcher), a column matcher over the whole "
                              "catalog that also picks each tool's table and columns, then "
                              "a passage reader over each passage. Without it rank 1 wins "
                              "on BM25 score, which over-answers when schema and data were "
                              "authored apart")
    add_model_options(model, JUDGE_MODULE, backing=JUDGE_BACKING)
    add_judge_options(model)
    return ap


def run(
    args: argparse.Namespace,
    console: Console,
    resolved: ResolvedBundle | None = None,
) -> FieldDrivenRun:
    """Route and compile, reporting through ``console``.

    The resolution is read from ``args.catalog``, unless a caller that already holds one
    in memory passes it as ``resolved`` — the app hands on the catalog its resolver page
    produced, without a file in between. What it built is returned as well, so a caller
    can render the routing itself rather than read the printed tables.
    """
    if resolved is None:
        if not args.catalog.is_file():
            raise SystemExit(
                f"No resolution at {args.catalog}. Save one with "
                f"`metadata-agent resolve --out {args.catalog}`, then rerun."
            )
        resolved = ResolvedBundle.load(args.catalog)
    judges = judges_from_args(args, console)

    console.print(f"[bold]bundle:[/] {resolved.root}")
    console.print(f"standard: {args.standard}")
    console.print(
        f"catalog:  {len(resolved.catalog.columns)} columns from "
        f"{[p.name for p in resolved.tables]}  (prose reader: {resolved.reader})"
    )
    searched = select(resolved.documents, args.search_doc)
    held_back = [p.name for p in resolved.documents if p not in searched]
    console.print(
        f"docs:     {[p.name for p in searched] or 'none'}  "
        f"candidates={args.candidates}  candidate-judge={judges.label}"
        + (f"  (not routed: {held_back})" if held_back else "")
    )

    field_plan, plan = route_and_compile(
        resolved, args.standard, candidates=args.candidates, judges=judges,
        documents=searched,
    )
    print_routing(field_plan, console)
    if args.debug:
        print_working(field_plan, console)
    print_plan(plan, console)
    return FieldDrivenRun(resolved, field_plan, plan, args.standard)


def main(argv: list[str] | None = None) -> None:
    console = Console()
    write_artifacts(run(build_parser().parse_args(argv), console), console)
