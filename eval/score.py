"""Grade a router configuration against a filled-in sheet.

Two numbers carry the design, and they answer different questions:

- **recall@k** — of the answerable fields, how many have their true answer anywhere
  in the ranked set: the ceiling on what rank 1 can get right. Reported only for a run
  **without** judges. With them nothing is filtered — the column matcher sees the whole
  catalog and the passage reader reads every passage — so there is no retrieval miss
  to count, and the number would be 100% by construction.
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
    judged: bool = False,
) -> Dict[str, float | None]:
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
    if answerable and not judged:
        console.print(
            f"  recall@{k}   [bold]{hit_at_k}/{len(answerable)}[/] "
            f"({hit_at_k / len(answerable):.0%})  — ceiling for rank 1"
        )
    elif answerable:
        console.print("  recall@k    [dim]not measured — the judges filter nothing out[/]")
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
    span_correct = sum(
        1 for s in span_scored if any(cites(quote, evidence[s.field]) for quote in s.quotes)
    )
    if span_scored:
        console.print(
            f"  cited correctly [bold]{span_correct}/{len(span_scored)}[/] of the "
            "span-labeled fields — a quote holds the labeled evidence, not just the "
            "right document"
        )
    several = [s for s in scored if s.routed and len(s.quotes) > 1]
    if several:
        console.print(f"  {len(several)} answered field(s) cited more than one quote")
    ungrounded = [s for s in scored if s.routed and s.grounded is False]
    if ungrounded:
        console.print(
            f"  [yellow]{len(ungrounded)} answered field(s) cited a quote that could "
            "not be located[/] — the router's own unreliability signal"
        )

    # The signals describe the lexical ranking; with judges on there is none to read.
    for signal in () if judged else SIGNALS:
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
        signals = () if judged else tuple(SIGNALS)
        for name in ("field", "answered with", "should be", "cited", *signals):
            table.add_column(name, overflow="fold")
        for s in sorted(wrong, key=lambda s: s.signals["coverage"]):
            table.add_row(
                s.field, s.top1 or "—",
                ALTERNATIVE_SEP.join(s.truth) or UNANSWERABLE,
                # The quote is what makes a document answer inspectable: two routings
                # naming the same file are told apart only by what they cited.
                _first(s.quotes),
                *(f"{s.signals[n]:.2f}" for n in signals),
            )
        console.print(table)

    return {
        "recall_at_k": None if judged else (hit_at_k / len(answerable) if answerable else 0.0),
        "precision_at_1": hit_at_1 / len(answerable) if answerable else 0.0,
        "over_answered": float(len(over)),
        "accuracy": correct / len(scored),
        "span_precision": span_correct / len(span_scored) if span_scored else 0.0,
    }


def _first(quotes) -> str:
    """The leading quote, shortened for a table cell; how many more there are."""
    if not quotes:
        return "—"
    head = quotes[0][:80] + ("…" if len(quotes[0]) > 80 else "")
    return head + (f" (+{len(quotes) - 1} more)" if len(quotes) > 1 else "")
