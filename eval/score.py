"""Grade a router configuration against a filled-in sheet.

Two numbers carry the design, and they answer different questions:

- **recall@k** — of the answerable fields, how many have their true answer anywhere
  in the ranked set. The ceiling on *any* re-ranking strategy, an LLM judge
  included, because a judge chooses among what retrieval surfaced and can never
  recover a miss. Unaffected by the judge.
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

from eval.labels import SIGNALS, UNANSWERABLE, ALTERNATIVE_SEP, Scored, cites


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


def report(
    scored: List[Scored],
    console: Console,
    k: int,
    evidence: Dict[str, str] | None = None,
) -> Dict[str, float]:
    """Print the grading and return the headline numbers for a caller to assert on."""
    if not scored:
        raise SystemExit(
            "The sheet has no filled-in answers yet. Fill the `answer` column with a "
            f"ref from sources.csv, or {UNANSWERABLE}, then rerun."
        )

    answerable = [s for s in scored if s.answerable]
    unanswerable = [s for s in scored if not s.answerable]
    hit_at_k = sum(1 for s in answerable if s.ranked_correct)
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

    by_judge = sum(1 for s in scored if s.abstained_by == "judge")
    if by_judge:
        console.print(f"  abstentions by the judge: {by_judge}")

    # Span precision: did the routing cite the right *passage*, not just the right
    # file? Only meaningful where the sheet labels the evidence, and it is the number
    # that separates a correct answer from one that named the same document by luck.
    span_scored = [s for s in scored if s.routed and (evidence or {}).get(s.field)]
    span_correct = sum(1 for s in span_scored if cites(s.quote, evidence[s.field]))
    if span_scored:
        console.print(
            f"  cited correctly [bold]{span_correct}/{len(span_scored)}[/] of the "
            "span-labeled fields — right passage, not just the right document"
        )
    ungrounded = [s for s in scored if s.routed and s.grounded is False]
    if ungrounded:
        console.print(
            f"  [yellow]{len(ungrounded)} answered field(s) cited a quote that could "
            "not be located[/] — the router's own unreliability signal"
        )

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
        for name in ("field", "answered with", "should be", "cited", *SIGNALS):
            table.add_column(name, overflow="fold")
        for s in sorted(wrong, key=lambda s: s.signals["coverage"]):
            table.add_row(
                s.field, s.top1 or "—",
                ALTERNATIVE_SEP.join(s.truth) or UNANSWERABLE,
                # The quote is what makes a document answer inspectable: two routings
                # naming the same file are told apart only by what they cited.
                (s.quote[:80] + "…" if len(s.quote) > 80 else s.quote) or "—",
                *(f"{s.signals[n]:.2f}" for n in SIGNALS),
            )
        console.print(table)

    return {
        "recall_at_k": hit_at_k / len(answerable) if answerable else 0.0,
        "precision_at_1": hit_at_1 / len(answerable) if answerable else 0.0,
        "over_answered": float(len(over)),
        "accuracy": correct / len(scored),
        "span_precision": span_correct / len(span_scored) if span_scored else 0.0,
    }
