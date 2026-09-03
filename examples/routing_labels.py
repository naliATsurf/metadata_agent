"""Build (and score) a hand-labeling sheet for router ground truth.

The router's abstention rule is ``score > 0`` — a non-emptiness test, not a
relevance test — and nothing here can be improved without knowing, per field,
what the *right* answer was. This script makes that knowable:

    # write the sheet, then fill in the `answer` column by hand
    python -m examples.routing_labels

    # once labeled, score the current router against it
    python -m examples.routing_labels --score

The sheet is deliberately not limited to what retrieval surfaced. A labeler may
name any column in ``sources.csv``, including one the router never ranked — that
is the only way to measure **recall**, and recall is what decides whether an LLM
re-ranker could ever help (it re-ranks; it cannot recover a miss).

Scoring reports the two numbers the design turns on:

- **recall@k** — the ceiling on any re-ranking strategy;
- a **risk-coverage curve** per candidate abstention signal (raw BM25, query-term
  coverage, rank-1 margin), which is the selective-prediction framing: accuracy
  among the fields answered, as a function of how many are answered. A signal
  that holds accuracy while shedding coverage is one worth thresholding on.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from rich.console import Console
from rich.table import Table

from src.context.base_context import EvidenceRef, content_terms, tokenize
from src.router.bundle import NONE, discover_bundle, select
from src.router.catalog import Catalog
from src.router.route import FieldPlan
from src.router.schema import FieldSpec, walk_schema
from src.standards import METADATA_STANDARDS, get_schema_for_standard

from examples.field_router_plan import (
    DEFAULT_BUNDLE,
    DEFAULT_STANDARD,
    build_plan,
)

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "data" / "eval"

# The label vocabulary. One string per answerable place, so a hand-written answer
# and a retrieved candidate reduce to the same token and compare with ``==``.
UNANSWERABLE = "NONE"
_ALTERNATIVE_SEP = "|"

# Document spans are labeled at *document* granularity: a human can say "the
# licence is stated in the README" but not which character offsets, and holding
# them to an offset would measure the chunker rather than the router.
_DOC_PREFIX = "doc::"
_TOOL_PREFIX = "tool::"


def ref_of(candidate: EvidenceRef) -> str:
    """The label token a retrieved candidate corresponds to."""
    if candidate.kind == "tool":
        return f"{_TOOL_PREFIX}{candidate.locator}"
    if candidate.kind == "quoted_span":
        return f"{_DOC_PREFIX}{candidate.resource}"
    return f"{candidate.resource}::{candidate.locator}"


def parse_answer(raw: str) -> List[str]:
    """Split a label cell into its allowed answers (``|``-separated)."""
    return [part.strip() for part in raw.split(_ALTERNATIVE_SEP) if part.strip()]


# ---------------------------------------------------------------------------
# The three abstention signals under test
# ---------------------------------------------------------------------------


def term_coverage(query: str, candidate: EvidenceRef) -> float:
    """Fraction of the query's content terms present in the candidate's text.

    Interpretable in a way a raw BM25 score is not: "won on 1 of 13 terms" is a
    claim a domain expert can check, and unlike the score it does not move with
    corpus size or query length.
    """
    terms = set(content_terms(query))
    if not terms:
        return 0.0
    return len(terms & set(tokenize(candidate.snippet or ""))) / len(terms)


def relative_margin(candidates: Sequence[EvidenceRef]) -> float:
    """How far rank 1 leads rank 2, as a fraction of rank 1's score.

    1.0 when rank 1 is unchallenged. A flat top-k means retrieval found nothing
    *discriminating*, which is different from finding nothing.
    """
    if not candidates or not candidates[0].score:
        return 0.0
    if len(candidates) == 1:
        return 1.0
    return (candidates[0].score - candidates[1].score) / candidates[0].score


SIGNALS = {
    "bm25": lambda routing: routing.candidates[0].score if routing.candidates else 0.0,
    "coverage": lambda routing: (
        term_coverage(routing.query, routing.candidates[0]) if routing.candidates else 0.0
    ),
    "margin": lambda routing: relative_margin(routing.candidates),
}


# ---------------------------------------------------------------------------
# Writing the sheet
# ---------------------------------------------------------------------------

_LABEL_HEADER = [
    "field", "answer", "notes", "description", "type", "required",
    "router_top1", "in_top5",
]


def _specs(standard: str) -> Dict[str, FieldSpec]:
    schema = get_schema_for_standard(standard)
    if schema is None:
        raise SystemExit(f"Unknown standard {standard!r}.")
    return {spec.path: spec for spec in walk_schema(schema)}


def write_sheet(
    field_plan: FieldPlan, standard: str, out: Path, k: int
) -> Tuple[Path, bool]:
    """Write the labeling sheet. Never overwrites a sheet that has labels in it."""
    specs = _specs(standard)
    header = _LABEL_HEADER + [f"rank{i + 1}" for i in range(k)]

    rows = []
    for path, routing in field_plan.routings.items():
        spec = specs.get(path)
        ranked = [
            f"{ref_of(c)} ({c.score:.2f})" for c in routing.candidates[:k]
        ]
        rows.append(
            {
                "field": path,
                "answer": "",           # <- to fill in
                "notes": "",
                "description": (spec.description if spec else routing.query) or "",
                "type": spec.type if spec else "",
                "required": "yes" if spec and spec.required else "no",
                "router_top1": ref_of(routing.candidates[0]) if routing.candidates else "",
                "in_top5": "",          # computed at score time
                **{f"rank{i + 1}": v for i, v in enumerate(ranked)},
            }
        )

    fresh = not _has_labels(out)
    target = out if fresh else out.with_suffix(".new.csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return target, fresh


def _has_labels(path: Path) -> bool:
    """True if the sheet exists and somebody has already filled something in."""
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(row.get("answer", "").strip() for row in csv.DictReader(handle))


def write_sources(catalog: Catalog, field_plan: FieldPlan, out: Path) -> Path:
    """Write the answer vocabulary: every place a field could legitimately come from.

    The point of the sheet is to catch answers retrieval *missed*, so the labeler
    needs the whole vocabulary in front of them, not only what was ranked.
    """
    retrieved = {
        ref_of(c) for r in field_plan.routings.values() for c in r.candidates
    }
    rows = []
    for column in catalog.columns:
        ref = f"{column.resource}::{column.name}"
        rows.append(
            {
                "ref": ref,
                "kind": "column",
                "resource": column.resource,
                "name": column.name,
                "dtype": column.dtype,
                "meaning": column.description or "",
                "units": column.units or "",
                "value_prior": column.value_label or "",
                "ever_retrieved": "yes" if ref in retrieved else "no",
            }
        )
    for tool in _answer_tools():
        ref = f"{_TOOL_PREFIX}{tool.name}"
        rows.append(
            {
                "ref": ref, "kind": "tool", "resource": "", "name": tool.name,
                "dtype": "", "meaning": tool.description or "", "units": "",
                "value_prior": "", "ever_retrieved": "yes" if ref in retrieved else "no",
            }
        )
    for ref in sorted(r for r in retrieved if r.startswith(_DOC_PREFIX)):
        rows.append(
            {
                "ref": ref, "kind": "document", "resource": ref[len(_DOC_PREFIX):],
                "name": "", "dtype": "", "meaning": "prose — cite the document, not a span",
                "units": "", "value_prior": "", "ever_retrieved": "yes",
            }
        )

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


# ---------------------------------------------------------------------------
# Scoring against a filled-in sheet
# ---------------------------------------------------------------------------


@dataclass
class Scored:
    """One field, its label, and what the router did with it."""

    field: str
    truth: List[str]                 # empty == labeled NONE (unanswerable)
    top1: Optional[str]
    ranked: List[str]
    routed: bool
    signals: Dict[str, float]

    @property
    def answerable(self) -> bool:
        return bool(self.truth)

    @property
    def top1_correct(self) -> bool:
        return self.top1 is not None and self.top1 in self.truth


def load_labels(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        raise SystemExit(f"No sheet at {path}. Run without --score to create it.")
    labels: Dict[str, List[str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("answer") or "").strip()
            if not raw:
                continue
            labels[row["field"]] = (
                [] if raw.upper() == UNANSWERABLE else parse_answer(raw)
            )
    return labels


def score(field_plan: FieldPlan, labels: Dict[str, List[str]]) -> List[Scored]:
    scored = []
    for path, routing in field_plan.routings.items():
        if path not in labels:
            continue
        ranked = [ref_of(c) for c in routing.candidates]
        scored.append(
            Scored(
                field=path,
                truth=labels[path],
                top1=ranked[0] if ranked else None,
                ranked=ranked,
                routed=routing.status != "unanswered" and bool(ranked),
                signals={name: fn(routing) for name, fn in SIGNALS.items()},
            )
        )
    return scored


def risk_coverage(scored: List[Scored], signal: str) -> List[Tuple[float, float, float]]:
    """Sweep a threshold on ``signal``; return (threshold, coverage, accuracy).

    Answering a field the label calls unanswerable counts as *wrong*, not as a
    non-event — over-answering is the failure mode being measured.
    """
    thresholds = sorted({s.signals[signal] for s in scored if s.routed})
    curve = []
    for threshold in thresholds:
        answered = [s for s in scored if s.routed and s.signals[signal] >= threshold]
        if not answered:
            continue
        correct = sum(1 for s in answered if s.answerable and s.top1_correct)
        curve.append((threshold, len(answered) / len(scored), correct / len(answered)))
    return curve


def report(scored: List[Scored], console: Console, k: int) -> None:
    if not scored:
        raise SystemExit(
            "The sheet has no filled-in answers yet. Fill the `answer` column "
            f"with a ref from sources.csv, or {UNANSWERABLE}, then rerun --score."
        )

    answerable = [s for s in scored if s.answerable]
    unanswerable = [s for s in scored if not s.answerable]

    console.print(f"\n[bold]Labeled:[/] {len(scored)} fields "
                  f"({len(answerable)} answerable, {len(unanswerable)} {UNANSWERABLE})")

    # Recall is the ceiling: a re-ranker — LLM or otherwise — can only choose from
    # what retrieval surfaced, so a field whose answer is absent here is lost
    # regardless of how good the reader is.
    hit_at_k = sum(1 for s in answerable if set(s.truth) & set(s.ranked))
    hit_at_1 = sum(1 for s in answerable if s.top1_correct)
    if answerable:
        console.print(
            f"  recall@{k}   [bold]{hit_at_k}/{len(answerable)}[/] "
            f"({hit_at_k / len(answerable):.0%})  — ceiling for any re-ranker"
        )
        console.print(
            f"  precision@1 {hit_at_1}/{len(answerable)} "
            f"({hit_at_1 / len(answerable):.0%})"
        )

    # The abstention question, stated directly.
    over = [s for s in unanswerable if s.routed]
    missed = [s for s in answerable if not s.routed]
    if unanswerable:
        console.print(
            f"  over-answered [bold]{len(over)}/{len(unanswerable)}[/] of the "
            f"{UNANSWERABLE} fields — routed something with no right answer"
        )
    if missed:
        console.print(f"  abstained on {len(missed)} field(s) that do have an answer")

    for signal in SIGNALS:
        curve = risk_coverage(scored, signal)
        if not curve:
            continue
        table = Table(title=f"Risk–coverage: {signal}", title_justify="left")
        table.add_column("threshold", justify="right")
        table.add_column("coverage", justify="right")
        table.add_column("accuracy", justify="right")
        # Show the knee, not every point: a handful of evenly spaced operating points.
        step = max(1, len(curve) // 8)
        for threshold, coverage, accuracy in curve[::step]:
            table.add_row(f"{threshold:.2f}", f"{coverage:.0%}", f"{accuracy:.0%}")
        console.print(table)

    wrong = [s for s in scored if s.routed and not s.top1_correct]
    if wrong:
        table = Table(title="Where rank 1 is wrong", title_justify="left")
        for name in ("field", "routed to", "should be", *SIGNALS):
            table.add_column(name, overflow="fold")
        for s in sorted(wrong, key=lambda s: s.signals["coverage"]):
            table.add_row(
                s.field, s.top1 or "—",
                _ALTERNATIVE_SEP.join(s.truth) or UNANSWERABLE,
                *(f"{s.signals[n]:.2f}" for n in SIGNALS),
            )
        console.print(table)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Create or score a router ground-truth labeling sheet."
    )
    source = ap.add_argument_group("Input", "The bundle whose routing is labeled.")
    target = ap.add_argument_group("Metadata standard", "The schema whose fields are routed.")
    mode = ap.add_argument_group("Mode", "Write the sheet, or score a filled-in one.")

    source.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="bundle directory")
    source.add_argument("--dictionary", action="append", default=None,
                        help=f"codebooks to use by filename (repeatable); '{NONE}' for none")
    source.add_argument("--doc", action="append", default=None,
                        help=f"documents to use by filename (repeatable); '{NONE}' for none")
    target.add_argument("--standard", default=DEFAULT_STANDARD,
                        choices=sorted(METADATA_STANDARDS),
                        help="metadata standard whose fields are routed")
    mode.add_argument("--candidates", type=int, default=5,
                      help="candidates per field — the recall budget the sheet measures")
    mode.add_argument("--score", action="store_true",
                      help="score the current router against the filled-in sheet")
    mode.add_argument("--out", type=Path, default=None,
                      help="sheet directory (default: data/eval/<standard>__<bundle>)")
    return ap


def run(args: argparse.Namespace, console: Console) -> Path:
    bundle = discover_bundle(args.bundle)
    catalog, field_plan, _ = build_plan(
        bundle.tables,
        select(bundle.codebooks, args.dictionary),
        select(bundle.documents, args.doc),
        args.standard,
        candidates=args.candidates,
    )

    out = args.out or (EVAL / f"{args.standard}__{args.bundle.name}")
    sheet = out / "labels.csv"

    if args.score:
        report(score(field_plan, load_labels(sheet)), console, args.candidates)
        return sheet

    written, fresh = write_sheet(field_plan, args.standard, sheet, args.candidates)
    sources = write_sources(catalog, field_plan, out / "sources.csv")
    if not fresh:
        console.print(
            f"[yellow]{sheet.name} already has labels — wrote {written.name} instead "
            "so your work is not overwritten.[/]"
        )
    console.print(f"[bold]sheet:[/]   {written}  ({len(field_plan.routings)} fields)")
    console.print(f"[bold]sources:[/] {sources}")
    console.print(
        f"\nFill the [bold]answer[/] column with a [bold]ref[/] from sources.csv "
        f"(several allowed, separated by '{_ALTERNATIVE_SEP}'), or [bold]{UNANSWERABLE}[/] "
        "if nothing in this bundle answers the field. Look past the rank columns — "
        "an answer the router never retrieved is exactly what this measures.\n"
        f"Then: [bold]python -m examples.routing_labels --score[/]"
    )
    return written


def main() -> None:
    run(build_parser().parse_args(), Console())


if __name__ == "__main__":
    main()
