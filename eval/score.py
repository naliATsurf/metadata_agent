"""Grade a router configuration against a filled-in sheet.

Two numbers carry the design, and they answer different questions:

- **recall@k** — of the answerable fields, how many have their true answer anywhere
  in the ranked set. The ceiling on *any* re-ranking strategy, an LLM judge
  included, because a judge chooses among what retrieval surfaced and can never
  recover a miss. Unaffected by the veto or the judge.
- **over-answered** — of the fields labeled ``NONE``, how many the router answered
  anyway. The abstention failure, counted directly. This is the one that moves.

The risk-coverage tables are the selective-prediction framing: accuracy among the
fields answered, as a function of how many are answered. A signal worth thresholding
on is one where accuracy *rises* as coverage falls; flat or falling means the signal
carries no information about its own reliability.

Answering a ``NONE`` field counts as **wrong**, never as a non-event. A metric that
ignores over-answering will always recommend answering more.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from rich.console import Console
from rich.table import Table

from eval.labels import SIGNALS, UNANSWERABLE, ALTERNATIVE_SEP, Scored


def risk_coverage(scored: List[Scored], signal: str) -> List[Tuple[float, float, float]]:
    """Sweep a threshold on ``signal``; return (threshold, coverage, accuracy)."""
    thresholds = sorted({s.signals[signal] for s in scored if s.routed})
    curve = []
    for threshold in thresholds:
        answered = [s for s in scored if s.routed and s.signals[signal] >= threshold]
        if not answered:
            continue
        correct = sum(1 for s in answered if s.answerable and s.top1_correct)
        curve.append((threshold, len(answered) / len(scored), correct / len(answered)))
    return curve


def report(scored: List[Scored], console: Console, k: int) -> Dict[str, float]:
    """Print the grading and return the headline numbers for a caller to assert on."""
    if not scored:
        raise SystemExit(
            "The sheet has no filled-in answers yet. Fill the `answer` column with a "
            f"ref from sources.csv, or {UNANSWERABLE}, then rerun."
        )

    answerable = [s for s in scored if s.answerable]
    unanswerable = [s for s in scored if not s.answerable]
    hit_at_k = sum(1 for s in answerable if set(s.truth) & set(s.ranked))
    hit_at_1 = sum(1 for s in answerable if s.top1_correct)
    over = [s for s in unanswerable if s.routed]
    missed = [s for s in answerable if not s.routed]
    correct = hit_at_1 + (len(unanswerable) - len(over))

    console.print(
        f"\n[bold]Labeled:[/] {len(scored)} fields "
        f"({len(answerable)} answerable, {len(unanswerable)} {UNANSWERABLE})"
    )
    if answerable:
        console.print(
            f"  recall@{k}   [bold]{hit_at_k}/{len(answerable)}[/] "
            f"({hit_at_k / len(answerable):.0%})  — ceiling for any re-ranker"
        )
        console.print(
            f"  precision@1 {hit_at_1}/{len(answerable)} "
            f"({hit_at_1 / len(answerable):.0%})"
        )
    if unanswerable:
        console.print(
            f"  over-answered [bold]{len(over)}/{len(unanswerable)}[/] of the "
            f"{UNANSWERABLE} fields — answered something with no right answer"
        )
    if missed:
        console.print(f"  abstained on {len(missed)} field(s) that do have an answer")
    console.print(f"  [bold]accuracy    {correct}/{len(scored)}[/] "
                  f"({correct / len(scored):.0%})")

    # Who did the abstaining — the deterministic veto, or the judge.
    by_veto = sum(1 for s in scored if s.abstained_by == "veto")
    by_judge = sum(1 for s in scored if s.abstained_by == "judge")
    if by_veto or by_judge:
        console.print(f"  abstentions: veto {by_veto}, judge {by_judge}")

    for signal in SIGNALS:
        curve = risk_coverage(scored, signal)
        if not curve:
            continue
        table = Table(title=f"Risk–coverage: {signal}", title_justify="left")
        for name in ("threshold", "coverage", "accuracy"):
            table.add_column(name, justify="right")
        step = max(1, len(curve) // 8)
        for threshold, coverage, accuracy in curve[::step]:
            table.add_row(f"{threshold:.2f}", f"{coverage:.0%}", f"{accuracy:.0%}")
        console.print(table)

    wrong = [s for s in scored if s.routed and not s.top1_correct]
    if wrong:
        table = Table(title="Where rank 1 is wrong", title_justify="left")
        for name in ("field", "answered with", "should be", *SIGNALS):
            table.add_column(name, overflow="fold")
        for s in sorted(wrong, key=lambda s: s.signals["coverage"]):
            table.add_row(
                s.field, s.top1 or "—",
                ALTERNATIVE_SEP.join(s.truth) or UNANSWERABLE,
                *(f"{s.signals[n]:.2f}" for n in SIGNALS),
            )
        console.print(table)

    return {
        "recall_at_k": hit_at_k / len(answerable) if answerable else 0.0,
        "precision_at_1": hit_at_1 / len(answerable) if answerable else 0.0,
        "over_answered": float(len(over)),
        "accuracy": correct / len(scored),
    }
