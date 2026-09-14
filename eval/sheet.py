"""Write the labeling sheet and the answer vocabulary — once per bundle.

The sheet is deliberately not limited to what retrieval surfaced. A labeler may name
any ref in ``sources.csv``, including one the router never ranked, because that is
the only way to measure **recall** — and recall decides whether a re-ranker could
ever help, since a judge re-ranks and cannot recover a miss.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple

from eval.labels import DOC_PREFIX, TOOL_PREFIX, ref_of
from src.router.catalog import Catalog
from src.router.route import FieldPlan
from src.router.schema import FieldSpec, walk_schema
from src.standards import get_schema_for_standard

_HEADER = [
    "field", "answer", "notes", "description", "type", "required", "router_top1",
]


def _specs(standard: str) -> Dict[str, FieldSpec]:
    schema = get_schema_for_standard(standard)
    if schema is None:
        raise SystemExit(f"Unknown standard {standard!r}.")
    return {spec.path: spec for spec in walk_schema(schema)}


def has_labels(path: Path) -> bool:
    """True if the sheet exists and somebody has already filled something in."""
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(row.get("answer", "").strip() for row in csv.DictReader(handle))


def write_sheet(
    field_plan: FieldPlan, standard: str, out: Path, k: int
) -> Tuple[Path, bool]:
    """Write the labeling sheet. **Never** overwrites a sheet that has labels in it.

    Returns the path written and whether it was the real sheet; a sheet already
    carrying answers is left alone and the new one lands beside it, because hours of
    hand-labeling must not be destroyed by rerunning a generator.
    """
    specs = _specs(standard)
    header = _HEADER + [f"rank{i + 1}" for i in range(k)]

    rows = []
    for path, routing in field_plan.routings.items():
        spec = specs.get(path)
        ranked = [f"{ref_of(c)} ({c.score:.2f})" for c in routing.candidates[:k]]
        rows.append({
            "field": path,
            "answer": "",                 # <- to fill in
            "notes": "",
            "description": (spec.description if spec else routing.query) or "",
            "type": spec.type if spec else "",
            "required": "yes" if spec and spec.required else "no",
            "router_top1": ref_of(routing.candidates[0]) if routing.candidates else "",
            **{f"rank{i + 1}": value for i, value in enumerate(ranked)},
        })

    fresh = not has_labels(out)
    target = out if fresh else out.with_suffix(".new.csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return target, fresh


def write_sources(catalog: Catalog, field_plan: FieldPlan, out: Path) -> Path:
    """Write the answer vocabulary: every place a field could legitimately come from.

    ``ever_retrieved`` marks the refs no field ranked. Those are the lexically
    invisible ones, and spotting them is most of the point: a bundle where half the
    vocabulary is never retrieved has a recall problem no judge can fix.
    """
    retrieved = {ref_of(c) for r in field_plan.routings.values() for c in r.candidates}
    rows: List[Dict[str, str]] = []
    for column in catalog.columns:
        ref = f"{column.resource}::{column.name}"
        rows.append({
            "ref": ref, "kind": "column", "resource": column.resource,
            "name": column.name, "dtype": column.dtype,
            "meaning": column.description or "", "units": column.units or "",
            "value_prior": column.value_label or "",
            "ever_retrieved": "yes" if ref in retrieved else "no",
        })
    for tool in _answer_tools():
        ref = f"{TOOL_PREFIX}{tool.name}"
        rows.append({
            "ref": ref, "kind": "tool", "resource": "", "name": tool.name,
            "dtype": "", "meaning": tool.description or "", "units": "",
            "value_prior": "", "ever_retrieved": "yes" if ref in retrieved else "no",
        })
    for ref in sorted(r for r in retrieved if r.startswith(DOC_PREFIX)):
        rows.append({
            "ref": ref, "kind": "document", "resource": ref[len(DOC_PREFIX):],
            "name": "", "dtype": "",
            "meaning": "prose — cite the document, not a span",
            "units": "", "value_prior": "", "ever_retrieved": "yes",
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return out


def _answer_tools():
    import src.tools  # noqa: F401 — importing registers them
    from src.tools.base import field_answering_tools

    return field_answering_tools()
