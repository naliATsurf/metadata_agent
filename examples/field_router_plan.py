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
from pathlib import Path
from typing import List, Tuple

import yaml

# Make the repo importable when run directly (python examples/field_router_plan.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.core.schemas import Plan
from src.router import Catalog, FieldPlan, compile_field_plan, resolve_bundle, route_fields
from src.standards import get_schema_for_standard

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "sample_output"

DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"
DEFAULT_STANDARD = "sharetrait_basic"


def discover(bundle: Path, dictionaries: List[str]) -> Tuple[List[Path], List[Path], List[Path]]:
    """Partition a bundle into data tables, dictionary CSVs, and documents."""
    csvs = sorted(bundle.glob("*.csv"))
    docs = sorted([*bundle.glob("*.md"), *bundle.glob("*.txt")])
    if not csvs:
        raise SystemExit(f"No CSV found in {bundle}. Add the data table(s).")

    dict_names = set(dictionaries or [])
    tables = [c for c in csvs if c.name not in dict_names]
    dicts = [c for c in csvs if c.name in dict_names]
    if not tables:
        raise SystemExit("Every CSV was marked --dictionary; at least one data table is needed.")
    return tables, dicts, docs


def build_plan(
    tables: List[Path], dicts: List[Path], docs: List[Path], standard: str
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

    catalog = resolve_bundle(table_ctx, sources=sources)           # layer 3 (all tables)
    field_plan = route_fields(schema, catalog=catalog, docs=doc_ctx)  # layer 4
    plan = compile_field_plan(field_plan)                          # layer 5
    return catalog, field_plan, plan


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _yaml(obj) -> str:
    """Clean YAML, normalizing tuples (span locators) to lists via a JSON round-trip."""
    return yaml.safe_dump(json.loads(json.dumps(obj, default=str)), sort_keys=False, width=100)


def print_catalog(catalog: Catalog) -> None:
    print("\n=== 1. Resolved catalog (columns across all tables) ===")
    print(f"{'table':<16}{'column':<24}{'method':<22}{'conf':<7}meaning")
    for c in catalog.columns:
        meaning = c.description or "(unresolved)"
        print(f"{c.resource:<16}{c.name:<24}{c.link_method:<22}{c.link_confidence:<7}{meaning}")
        for msg in c.conflicts:
            print(f"{'':<16}conflict: {msg}")


def print_routing(field_plan: FieldPlan) -> None:
    print("\n=== 2. Field routing (which table/column answers each field) ===")
    print(f"{'field':<28}{'bucket':<22}{'assurance':<10}source")
    for path, r in field_plan.routings.items():
        if r.candidates:
            c = r.candidates[0]
            src = f"{c.resource}:{c.locator}" if c.resource else str(c.locator)
        else:
            src = "—"
        print(f"{path:<28}{r.bucket:<22}{r.assurance:<10}{src}")
    cov = field_plan.coverage()
    print(f"\ncoverage: {cov['routed']}/{cov['total']} routed, "
          f"unresolved={cov['unresolved']}, by_bucket={cov['by_bucket']}")


def print_plan(plan: Plan) -> None:
    print("\n=== 3. Compiled plan (one extraction task per table) ===")
    for i, t in enumerate(plan.steps):
        scope = t.target_resources or ["<context>"]
        print(f"[{i}] task={t.task:<26} player={t.player:<19} topology={t.topology or '-':<7} "
              f"scope={scope} fields={len(t.fields)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a field-driven plan for a multi-table bundle.")
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="bundle directory")
    ap.add_argument("--standard", default=DEFAULT_STANDARD, help="metadata standard name")
    ap.add_argument("--dictionary", action="append", default=[],
                    help="a codebook CSV to treat as a source, not a data table (repeatable)")
    args = ap.parse_args()

    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(
            f"Bundle {args.bundle} is missing or empty. Put the data table(s) there "
            f"(plus any codebook / README), then rerun."
        )

    tables, dicts, docs = discover(args.bundle, args.dictionary)
    print(f"bundle:   {args.bundle}")
    print(f"standard: {args.standard}")
    print(f"tables:   {[p.name for p in tables]}")
    print(f"sources:  dictionaries={[p.name for p in dicts] or 'none'}  docs={[p.name for p in docs] or 'none'}")

    catalog, field_plan, plan = build_plan(tables, dicts, docs, args.standard)
    print_catalog(catalog)
    print_routing(field_plan)
    print_plan(plan)

    OUT.mkdir(parents=True, exist_ok=True)
    fp_path = OUT / f"{args.standard}_field_plan.yaml"
    plan_path = OUT / f"{args.standard}_compiled_plan.yaml"
    fp_path.write_text(_yaml(field_plan.to_dict()))
    plan_path.write_text(_yaml({"plan": plan.model_dump()}))
    print(f"\nWrote {fp_path} and {plan_path}.")


if __name__ == "__main__":
    main()
