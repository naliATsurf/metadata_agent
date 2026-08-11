"""Example: build a field-driven extraction plan for a dataset bundle.

Runs the field-driven pipeline end to end, deterministically and with no LLM calls:

    resolve_catalog  →  route_fields  →  compile_field_plan
    (layer 3)           (layer 4)        (layer 5)

Given a target data table plus its auxiliary files (a codebook, a README), it:

  1. **resolves the catalog** — turns opaque column names into described columns by
     harvesting meanings from the codebook / prose, cross-checked against the values;
  2. **routes each schema field** to where it is answered — a whole-resource tool
     (`structural`), a resolved column (`ambiguous_structural`), or a document span
     (`narrative`) — leaving unanswerable fields `unresolved` *before* extraction;
  3. **compiles** the routing into an executable `Plan` of `Task`s, each carrying
     per-field *ranked candidate sets* the executor selects from.

Usage:

    # default: the sharetrait bundle + the sharetrait_basic standard
    python examples/field_router_plan.py

    # any bundle + standard; --target names the main data table when a bundle has
    # more than one CSV (the rest become auxiliary sources / documents)
    python examples/field_router_plan.py --bundle data/tests/router_test \\
        --standard field_router_test --target observations.csv

The bundle is a directory of files: CSVs are tabular resources (one is the target
data table, the others are candidate data dictionaries); .md / .txt files are
documents. Files are auto-discovered.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

# Make the repo importable when run directly (python examples/field_router_plan.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.context.text_context import TextContext
from src.core.schemas import Plan
from src.router import Catalog, FieldPlan, compile_field_plan, resolve_catalog, route_fields
from src.standards import get_schema_for_standard

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "sample_output"

DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"
DEFAULT_STANDARD = "sharetrait_basic"


def discover(bundle: Path, target: Optional[str]) -> Tuple[Path, List[Path]]:
    """Pick the target data table and the auxiliary files from a bundle directory."""
    csvs = sorted(bundle.glob("*.csv"))
    docs = sorted([*bundle.glob("*.md"), *bundle.glob("*.txt")])
    if not csvs:
        raise SystemExit(f"No CSV found in {bundle}. Add the data table (and any codebook / README).")

    if target:
        target_csv = bundle / target
        if not target_csv.exists():
            raise SystemExit(f"--target {target!r} not found in {bundle}.")
    elif len(csvs) == 1:
        target_csv = csvs[0]
    else:
        names = [c.name for c in csvs]
        raise SystemExit(
            f"{len(csvs)} CSVs in {bundle}; pass --target <file> to name the main data "
            f"table. Found: {names}"
        )

    aux = [p for p in (*csvs, *docs) if p != target_csv]
    return target_csv, aux


def build_plan(
    target_csv: Path, aux_files: List[Path], standard: str
) -> Tuple[Catalog, FieldPlan, Plan]:
    """The three-line core: resolve → route → compile.

    ``aux_files`` are auto-classified by :func:`create_context`: a CSV becomes a
    tabular source (a possible data dictionary for the catalog), a markdown/text file
    becomes a document (prose definitions for the catalog *and* a narrative surface).
    """
    schema = get_schema_for_standard(standard)
    if schema is None:
        raise SystemExit(f"Unknown standard {standard!r}.")

    target = create_context(str(target_csv), name=target_csv.stem)
    aux = [create_context(str(p), name=p.stem) for p in aux_files]
    docs = [c for c in aux if isinstance(c, TextContext)]

    catalog = resolve_catalog(target, sources=aux)                 # layer 3
    field_plan = route_fields(schema, catalog=catalog, docs=docs)  # layer 4
    plan = compile_field_plan(field_plan)                          # layer 5
    return catalog, field_plan, plan


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _yaml(obj) -> str:
    """Clean YAML, normalizing tuples (span locators) to lists via a JSON round-trip."""
    return yaml.safe_dump(json.loads(json.dumps(obj, default=str)), sort_keys=False, width=100)


def print_catalog(catalog: Catalog) -> None:
    print("\n=== 1. Resolved catalog (opaque columns → described columns) ===")
    print(f"{'column':<24}{'method':<22}{'conf':<8}{'label':<12}meaning")
    for c in catalog.columns:
        meaning = c.description or "(unresolved)"
        print(f"{c.name:<24}{c.link_method:<22}{c.link_confidence:<8}{c.value_label or '-':<12}{meaning}")
        for msg in c.conflicts:
            print(f"{'':<24}conflict: {msg}")


def print_routing(field_plan: FieldPlan) -> None:
    print("\n=== 2. Field routing (where each field is answered) ===")
    print(f"{'field':<28}{'bucket':<22}{'assurance':<10}top candidate")
    for path, r in field_plan.routings.items():
        top = f"{r.candidates[0].locator}" if r.candidates else "—"
        print(f"{path:<28}{r.bucket:<22}{r.assurance:<10}{top}")
    cov = field_plan.coverage()
    print(f"\ncoverage: {cov['routed']}/{cov['total']} routed, "
          f"unresolved={cov['unresolved']}, by_bucket={cov['by_bucket']}")


def print_plan(plan: Plan) -> None:
    print("\n=== 3. Compiled plan (executable Task list) ===")
    for i, t in enumerate(plan.steps):
        scope = t.target_resources or ["<context>"]
        print(f"[{i}] task={t.task:<26} player={t.player:<19} topology={t.topology or '-':<7} "
              f"scope={scope} fields={t.fields}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a field-driven plan for a dataset bundle.")
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="bundle directory")
    ap.add_argument("--standard", default=DEFAULT_STANDARD, help="metadata standard name")
    ap.add_argument("--target", default=None, help="the main data-table CSV (if the bundle has several)")
    args = ap.parse_args()

    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(
            f"Bundle {args.bundle} is missing or empty. Put the data table there (plus any "
            f"codebook / README), then rerun."
        )

    target_csv, aux = discover(args.bundle, args.target)
    print(f"bundle:   {args.bundle}")
    print(f"standard: {args.standard}")
    print(f"target:   {target_csv.name}   aux: {[p.name for p in aux] or 'none'}")

    catalog, field_plan, plan = build_plan(target_csv, aux, args.standard)
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
