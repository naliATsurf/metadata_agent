"""The label vocabulary, and what a router run scored against it.

A label and a retrieved candidate must reduce to the same token so they compare
with ``==``; the scheme itself lives in the library
(:func:`~src.router.judge.candidate_ref`) because the candidate judge picks by the
same identifiers a human labels with. A pick and a label are then comparable
without translation, which is the only reason scoring a judge is cheap.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.context.base_context import EvidenceRef, content_terms, tokenize
from src.router.judge import candidate_ref
from src.router.route import FieldPlan

#: The label meaning "nothing in this bundle answers this field" — the single most
#: valuable cell in the sheet, because knowing when to abstain is what is measured.
UNANSWERABLE = "NONE"
ALTERNATIVE_SEP = "|"

# Document spans are labeled at *document* granularity: a human can say "the licence
# is stated in the README" but not which character offsets, and holding them to an
# offset would measure the chunker rather than the router.
DOC_PREFIX = "doc::"
TOOL_PREFIX = "tool::"

ref_of = candidate_ref


def parse_answer(raw: str) -> List[str]:
    """Split a label cell into its allowed answers (``|``-separated)."""
    return [part.strip() for part in raw.split(ALTERNATIVE_SEP) if part.strip()]


def load_labels(path: Path) -> Dict[str, List[str]]:
    """Read a filled-in sheet: field path -> allowed answers (empty list == NONE)."""
    if not path.exists():
        raise SystemExit(f"No sheet at {path}. Run `python -m eval sheet` first.")
    labels: Dict[str, List[str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("answer") or "").strip()
            if raw:
                labels[row["field"]] = (
                    [] if raw.upper() == UNANSWERABLE else parse_answer(raw)
                )
    return labels


# ---------------------------------------------------------------------------
# Abstention signals under test
# ---------------------------------------------------------------------------


def term_coverage(query: str, candidate: EvidenceRef) -> float:
    """Fraction of the query's content terms present in the candidate's text.

    Interpretable where a raw BM25 score is not: "won on 1 of 13 terms" is a claim a
    domain expert can check, and unlike the score it does not move with corpus size
    or query length.
    """
    terms = set(content_terms(query))
    if not terms:
        return 0.0
    return len(terms & set(tokenize(candidate.snippet or ""))) / len(terms)


def relative_margin(candidates: Sequence[EvidenceRef]) -> float:
    """How far rank 1 leads rank 2, as a fraction of rank 1's score.

    1.0 when rank 1 is unchallenged. A flat top-k means retrieval found nothing
    *discriminating*, which is different from finding nothing. Goes **negative** when
    a candidate judge promoted a candidate BM25 ranked lower — all three signals here
    describe the lexical router, so reading them after adjudication says only how far
    the judge departed from the ranking.
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
# Scoring
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
    abstained_by: Optional[str] = None   # "veto" | "judge" | None
    quote: str = ""                      # the sentence the judge cited
    grounded: Optional[bool] = None      # was that quote found in the chosen material?

    #: Set by :func:`score` when the sheet labels this field's evidence: the ref
    #: matched *and* the passage actually contains it. ``None`` means ref-level only.
    top1_precise: Optional[bool] = None
    ranked_precise: Optional[bool] = None

    @property
    def answerable(self) -> bool:
        return bool(self.truth)

    @property
    def top1_correct(self) -> bool:
        if self.top1_precise is not None:
            return self.top1_precise
        return self.top1 is not None and self.top1 in self.truth

    @property
    def ranked_correct(self) -> bool:
        """Did retrieval surface the answer anywhere in the ranked set?"""
        if self.ranked_precise is not None:
            return self.ranked_precise
        return bool(set(self.truth) & set(self.ranked))


def points_at(
    candidate: EvidenceRef, truth: Sequence[str], evidence: str, passage
) -> bool:
    """Does this candidate point at the labeled answer — the passage, not just the file?

    For a column or a tool the ref settles it: ``growth::pH`` names one place. For a
    document it does not. ``doc::readme_long`` is one label for 34 000 characters, so
    *any* chunk of that file counts as the right answer and recall@k degrades into
    "was the right file retrieved" — which, in a three-document bundle, is no
    measurement at all. Where the sheet labels the evidence, the passage must actually
    contain it.
    """
    if ref_of(candidate) not in truth:
        return False
    if not evidence or candidate.kind != "quoted_span" or passage is None:
        return True
    return cites(passage(candidate) or "", evidence)


def score(
    field_plan: FieldPlan,
    labels: Dict[str, List[str]],
    evidence: Optional[Dict[str, str]] = None,
    passage=None,
) -> List[Scored]:
    """Join a routed FieldPlan to the labels, field by field.

    ``evidence`` and ``passage`` together sharpen a document label from the file to
    the passage; without them the join is ref-level, as before.
    """
    evidence = evidence or {}
    scored: List[Scored] = []
    for path, routing in field_plan.routings.items():
        if path not in labels:
            continue
        ranked = [ref_of(c) for c in routing.candidates]
        want = evidence.get(path, "")
        precise = (
            [points_at(c, labels[path], want, passage) for c in routing.candidates]
            if want and passage is not None
            else []
        )
        routed = routing.status != "unanswered" and bool(ranked)
        by = None
        if not routed:
            by = "judge" if routing.judge_note else ("veto" if routing.vetoed else None)
        scored.append(
            Scored(
                field=path,
                truth=labels[path],
                # An abstention is not a pick. A rejected set stays in ``ranked`` —
                # recall@k measures *retrieval* and is unaffected by the judge — but
                # nothing is credited at rank 1.
                top1=ranked[0] if routed else None,
                ranked=ranked,
                routed=routed,
                signals={name: fn(routing) for name, fn in SIGNALS.items()},
                abstained_by=by,
                quote=routing.judge_quote or "",
                grounded=routing.judge_grounded,
                top1_precise=(precise[0] and routed) if precise else None,
                ranked_precise=any(precise) if precise else None,
            )
        )
    return scored


def load_evidence(path: Path) -> Dict[str, str]:
    """Read the optional ``evidence`` column: what the right quote must contain.

    A ref alone cannot grade a document answer. ``doc::readme_long`` is one label for
    a 34 000-character file, so a routing that cites the lab bench temperature and one
    that cites the acclimation temperature score identically — the metric measures
    which *file* was named, which for a single-document bundle is no measurement at
    all. A few words the correct passage must contain are cheap to label and restore
    the distinction, and unlike a character offset a human can actually write them.

    Optional per field: unlabeled fields are simply not span-scored.
    """
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["field"]: (row.get("evidence") or "").strip()
            for row in csv.DictReader(handle)
            if (row.get("evidence") or "").strip()
        }


def cites(quote: str, evidence: str) -> bool:
    """Does the cited quote contain the labeled evidence? Whitespace- and case-loose."""
    if not quote or not evidence:
        return False
    return " ".join(evidence.casefold().split()) in " ".join(quote.casefold().split())
