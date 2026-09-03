"""Example: build a field-driven extraction plan for a dataset bundle.

Runs the field-driven pipeline end to end, deterministically and with no LLM calls:

    resolve_bundle   →  route_fields  →  compile_field_plan
    (layer 3)           (layer 4)        (layer 5)

A real repository is **many tables** — the fields of one schema are answered by
columns in *different* CSVs (a dataset table, a measurement table, a taxonomy
table). This resolves *every* data table into one catalog spanning all their
columns (`resolve_bundle`), routes each schema field to whichever table's column
(or document span) answers it, and compiles the routing into a `Plan` whose
extraction tasks are grouped per table.

Usage:

    # default: the sharetrait bundle + the sharetrait_basic standard
    python examples/field_router_plan.py

    # any bundle + standard; mark codebook CSVs as dictionaries (sources, not tables)
    python examples/field_router_plan.py --bundle data/tests/router_test \\
        --standard field_router_test --dictionary codebook.csv

Every CSV in the bundle is treated as a data table unless named with --dictionary;
.md / .txt files are documents (prose definitions + a narrative surface).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import yaml

# Make the repo importable when run directly (python examples/field_router_plan.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console

from src.context import create_context
from src.core.schemas import Plan
from src.router import (
    NONE,
    Catalog,
    DeterministicProseReader,
    FieldPlan,
    ProseReader,
    compile_field_plan,
    discover_bundle,
    resolve_bundle,
    route_fields,
    select,
)
from src.standards import METADATA_STANDARDS, get_schema_for_standard

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "sample_output"

DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"
DEFAULT_STANDARD = "sharetrait_basic"


def build_plan(
    tables: List[Path], dicts: List[Path], docs: List[Path], standard: str,
    prose_reader: ProseReader | None = None,
    candidates: int = 5,
) -> Tuple[Catalog, FieldPlan, Plan]:
    """The core: resolve the whole bundle → route → compile."""
    schema = get_schema_for_standard(standard)
    if schema is None:
        raise SystemExit(f"Unknown standard {standard!r}.")

    table_ctx = [create_context(str(p), name=p.stem) for p in tables]
    doc_ctx = [create_context(str(p), name=p.stem) for p in docs]
    dict_ctx = [create_context(str(p), name=p.stem) for p in dicts]
    # Codebooks and documents describe columns wherever they live, so they are
    # offered to every table's resolution.
    sources = dict_ctx + doc_ctx

    catalog = resolve_bundle(table_ctx, sources=sources, prose_reader=prose_reader)  # layer 3
    field_plan = route_fields(schema, catalog=catalog, docs=doc_ctx, k=candidates)  # layer 4
    plan = compile_field_plan(field_plan)                          # layer 5
    return catalog, field_plan, plan


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _yaml(obj) -> str:
    """Clean YAML, normalizing tuples (span locators) to lists via a JSON round-trip."""
    return yaml.safe_dump(json.loads(json.dumps(obj, default=str)), sort_keys=False, width=100)


def print_catalog(catalog: Catalog, console: Console) -> None:
    """The resolved columns across every table."""
    console.print("\n[bold]1. Resolved catalog (columns across all tables)[/]")
    console.print(f"{'table':<16}{'column':<24}{'method':<22}{'conf':<7}meaning", style="dim")
    for c in catalog.columns:
        meaning = c.description or "(unresolved)"
        console.print(
            f"{c.resource:<16}{c.name:<24}{c.link_method:<22}{c.link_confidence:<7}{meaning}"
        )
        for msg in c.conflicts:
            console.print(f"{'':<16}conflict: {msg}", style="yellow")


def print_routing(field_plan: FieldPlan, console: Console) -> None:
    """Which table, column, or document span answers each schema field."""
    console.print("\n[bold]2. Field routing (which table/column answers each field)[/]")
    console.print(f"{'field':<28}{'bucket':<22}{'assurance':<10}source", style="dim")
    for path, r in field_plan.routings.items():
        console.print(f"{path:<28}{r.bucket:<22}{r.assurance:<10}{_source_of(r)}")
    cov = field_plan.coverage()
    console.print(
        f"\n[bold]coverage:[/] {cov['routed']}/{cov['total']} routed, "
        f"unanswered={cov['unanswered']}, by_bucket={cov['by_bucket']}"
    )


def _source_of(routing) -> str:
    """The routing's top candidate, rendered as ``resource:locator``."""
    if not routing.candidates:
        return "—"
    c = routing.candidates[0]
    return f"{c.resource}:{c.locator}" if c.resource else str(c.locator)


def print_plan(plan: Plan, console: Console) -> None:
    """The compiled plan: one extraction task per table."""
    console.print("\n[bold]3. Compiled plan (one extraction task per table)[/]")
    for i, t in enumerate(plan.steps):
        scope = t.target_resources or ["<context>"]
        console.print(
            f"[{i}] task={t.task:<26} player={t.player:<19} "
            f"topology={t.topology or '-':<7} scope={scope} fields={len(t.fields)}"
        )


@dataclass(frozen=True)
class RouterResult:
    """Everything one run produced, for a caller that renders it itself."""

    catalog: Catalog
    field_plan: FieldPlan
    plan: Plan
    standard: str


def build_parser() -> argparse.ArgumentParser:
    """The example's argument surface, built separately so a UI can render it."""
    ap = argparse.ArgumentParser(
        description="Build a field-driven plan for a multi-table bundle."
    )
    source = ap.add_argument_group(
        "Input", "The bundle, and which of its discovered sources to resolve from."
    )
    target = ap.add_argument_group(
        "Metadata standard", "The schema whose fields are routed."
    )
    routing = ap.add_argument_group(
        "Routing", "How many candidates the router keeps per field."
    )
    tier = ap.add_argument_group(
        "Prose tiers", "Which readers run above the codebook and the value prior."
    )

    source.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE,
                        help="bundle directory")
    target.add_argument("--standard", default=DEFAULT_STANDARD,
                        choices=sorted(METADATA_STANDARDS),
                        help="metadata standard whose fields are routed")
    source.add_argument("--dictionary", action="append", default=None,
                        help="codebooks to use, by filename (repeatable). Omit to use every "
                             f"codebook found in the bundle; pass '{NONE}' to use none")
    source.add_argument("--doc", action="append", default=None,
                        help="documents to use, by filename (repeatable). Omit to use every "
                             f"document found in the bundle; pass '{NONE}' to use none")
    routing.add_argument("--candidates", type=int, default=5,
                         help="how many ranked candidates to keep per field. The router "
                              "proposes a set and the executor picks from it, so this is "
                              "the recall budget, not a display setting")
    tier.add_argument("--prose-reader", action="store_true",
                      help="enable the retrieve-then-read prose tier (localize + read a "
                           "cued definition) above the glossary regex")
    return ap


def run(args: argparse.Namespace, console: Console) -> RouterResult:
    """Resolve, route, and compile, reporting through ``console``.

    Returns what it built as well, so a caller can render the routing itself rather
    than read the printed tables.
    """
    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(
            f"Bundle {args.bundle} is missing or empty. Put the data table(s) there "
            f"(plus any codebook / README), then rerun."
        )
    try:
        bundle = discover_bundle(args.bundle)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    dicts = select(bundle.codebooks, args.dictionary)
    docs = select(bundle.documents, args.doc)
    reader = DeterministicProseReader() if args.prose_reader else None

    console.print(f"[bold]bundle:[/] {args.bundle}")
    console.print(f"standard: {args.standard}")
    console.print(f"tables:   {[p.name for p in bundle.tables]}")
    console.print(
        f"sources:  dictionaries={[p.name for p in dicts] or 'none'}  "
        f"docs={[p.name for p in docs] or 'none'}  "
        f"prose-reader={'on' if reader else 'off'}  candidates={args.candidates}"
    )
    excluded = [
        p.name for p in (*bundle.codebooks, *bundle.documents) if p not in (*dicts, *docs)
    ]
    if excluded:
        console.print(f"[dim]discovered but not used: {excluded}[/]")

    catalog, field_plan, plan = build_plan(
        bundle.tables, dicts, docs, args.standard,
        prose_reader=reader, candidates=args.candidates,
    )
    print_catalog(catalog, console)
    print_routing(field_plan, console)
    print_plan(plan, console)

    return RouterResult(catalog, field_plan, plan, args.standard)


def write_artifacts(result: RouterResult, console: Console) -> List[Path]:
    """Persist the field plan and the compiled plan, and say where they went.

    Kept out of :func:`run` so that producing artifacts is a command-line act. A UI
    driving ``run`` is trying inputs, not building outputs, and should not overwrite
    the checked-in plans on every click.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    field_plan_path = OUT / f"{result.standard}_field_plan.yaml"
    plan_path = OUT / f"{result.standard}_compiled_plan.yaml"
    field_plan_path.write_text(_yaml(result.field_plan.to_dict()))
    plan_path.write_text(_yaml({"plan": result.plan.model_dump()}))
    console.print(f"\nWrote {field_plan_path} and {plan_path}.")
    return [field_plan_path, plan_path]


def main() -> None:
    console = Console()
    write_artifacts(run(build_parser().parse_args(), console), console)


if __name__ == "__main__":
    main()
