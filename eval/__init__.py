"""Router evaluation: hand-labeled ground truth, and scoring against it.

The router's abstention rule is a non-emptiness test on a BM25 score, and nothing
about it can be improved without knowing, per field, what the *right* answer was.
This package makes that knowable, in two halves that are used at different rates:

- :mod:`eval.sheet` writes the labeling sheet — **once per bundle**, then a human
  fills in the ``answer`` column;
- :mod:`eval.score` grades a router configuration against a filled-in sheet —
  **every time the router changes**.

The second is the one that keeps earning: a routing regression is silent, because
a worse router looks exactly like a better one until something counts its answers.
"""

from eval.labels import UNANSWERABLE, Scored, load_labels, ref_of, score
from eval.score import report
from eval.sheet import write_sheet, write_sources

__all__ = [
    "UNANSWERABLE",
    "Scored",
    "load_labels",
    "ref_of",
    "report",
    "score",
    "write_sheet",
    "write_sources",
]
