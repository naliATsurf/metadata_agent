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
  3. **compiles** the routing into an executable `Plan` of `Task`s (the same shape
     the source-driven planner emits, so the executor runs it unchanged), where each
     task carries per-field *ranked candidate sets* the executor selects from.

Run it:

    python examples/field_router_plan.py

It prints the resolved catalog, the routing coverage, and the compiled plan, and
writes the FieldPlan and Plan to ``data/sample_output/``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Tuple

import yaml

# Make the repo importable when run directly (python examples/field_router_plan.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.context.text_context import TextContext
from src.router import Catalog, FieldPlan, compile_field_plan, resolve_catalog, route_fields
from src.core.schemas import Plan
from src.standards import get_schema_for_standard

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "data" / "tests" / "router_test"
OUT = REPO / "data" / "sample_output"


def build_plan(
    target_csv: Path, aux_files: List[Path], standard: str
) -> Tuple[Catalog, FieldPlan, Plan]:
    """The three-line core: resolve → route → compile.

    ``aux_files`` are auto-classified by :func:`create_context`: a CSV becomes a
    tabular source (a possible data dictionary for the catalog), a markdown/text file
    becomes a document (a source of prose definitions *and* a narrative surface to
    route over). The target table's columns are what the catalog resolves.
    """
    target = create_context(str(target_csv), name=target_csv.stem)
    aux = [create_context(str(p), name=p.stem) for p in aux_files]
    docs = [c for c in aux if isinstance(c, TextContext)]  # text files are routable prose

    schema = get_schema_for_standard(standard)
    if schema is None:
        raise SystemExit(f"Unknown standard '{standard}'.")

    catalog = resolve_catalog(target, sources=aux)          # layer 3
    field_plan = route_fields(schema, catalog=catalog, docs=docs)  # layer 4
    plan = compile_field_plan(field_plan)                   # layer 5
    return catalog, field_plan, plan


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


def _yaml(obj) -> str:
    """Clean YAML, normalizing tuples (span locators) to lists via a JSON round-trip."""
    return yaml.safe_dump(json.loads(json.dumps(obj, default=str)), sort_keys=False, width=100)


def print_catalog(catalog: Catalog) -> None:
    print("\n=== 1. Resolved catalog (opaque columns → described columns) ===")
    print(f"{'column':<10}{'method':<22}{'conf':<8}{'label':<12}meaning")
    for c in catalog.columns:
        meaning = c.description or "(unresolved)"
        print(f"{c.name:<10}{c.link_method:<22}{c.link_confidence:<8}{c.value_label or '-':<12}{meaning}")
        for msg in c.conflicts:
            print(f"{'':<10}conflict: {msg}")


def print_routing(field_plan: FieldPlan) -> None:
    print("\n=== 2. Field routing (where each field is answered) ===")
    print(f"{'field':<20}{'bucket':<22}{'assurance':<10}top candidate")
    for path, r in field_plan.routings.items():
        top = f"{r.candidates[0].locator}" if r.candidates else "—"
        print(f"{path:<20}{r.bucket:<22}{r.assurance:<10}{top}")
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
    if not BUNDLE.exists():
        raise SystemExit(f"Bundle not found at {BUNDLE}.")

    catalog, field_plan, plan = build_plan(
        target_csv=BUNDLE / "observations.csv",
        aux_files=[BUNDLE / "codebook.csv", BUNDLE / "README.md"],
        standard="field_router_test",
    )

    print_catalog(catalog)
    print_routing(field_plan)
    print_plan(plan)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "field_plan.yaml").write_text(_yaml(field_plan.to_dict()))
    (OUT / "compiled_plan.yaml").write_text(_yaml({"plan": plan.model_dump()}))
    print(f"\nWrote {OUT / 'field_plan.yaml'} and {OUT / 'compiled_plan.yaml'}.")


if __name__ == "__main__":
    main()
