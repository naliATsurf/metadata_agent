"""Tests for the evaluation harness (see eval/).

The harness is how a routing regression becomes visible, so its own semantics have
to be right: an abstention must not be credited with a pick, recall must stay a
statement about *retrieval*, and a filled-in sheet must never be overwritten.
"""

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console

from eval.labels import UNANSWERABLE, load_labels, parse_answer, ref_of, score
from eval.score import report, risk_coverage
from eval.sheet import has_labels
from src.context.base_context import EvidenceRef
from src.router.route import FieldPlan, FieldRouting


def _ref(resource, locator, kind="computed_column", score_=1.0):
    return EvidenceRef(resource=resource, locator=locator, kind=kind,
                       snippet=f"{locator} snippet", score=score_)


def _plan(**routings) -> FieldPlan:
    return FieldPlan(schema_name="T", routings=routings)


class LabelVocabularyTest(unittest.TestCase):
    def test_alternatives_split_on_the_pipe(self):
        self.assertEqual(parse_answer("a::b | c::d"), ["a::b", "c::d"])

    def test_a_label_and_a_candidate_reduce_to_the_same_token(self):
        """The whole harness rests on this: no translation between the two."""
        self.assertEqual(ref_of(_ref("growth", "pH")), "growth::pH")


class ScoringTest(unittest.TestCase):
    def _routing(self, path, candidates, status="routed", **kw):
        return FieldRouting(field_path=path, query="q", bucket="column",
                            candidates=candidates, status=status, **kw)

    def test_an_abstained_field_is_credited_with_no_pick(self):
        plan = _plan(f=self._routing(
            "f", [_ref("t", "a")], status="unanswered",
            judge_note="nothing answers this",
        ))
        [scored] = score(plan, {"f": ["t::a"]})
        self.assertFalse(scored.routed)
        self.assertIsNone(scored.top1)
        self.assertFalse(scored.top1_correct)

    def test_recall_survives_an_abstention(self):
        """Recall measures retrieval, so rejecting a set does not erase it."""
        plan = _plan(f=self._routing("f", [_ref("t", "a")], status="unanswered"))
        [scored] = score(plan, {"f": ["t::a"]})
        self.assertIn("t::a", scored.ranked)

    def test_abstention_is_attributed_to_the_veto_or_the_judge(self):
        plan = _plan(
            byveto=self._routing("byveto", [], status="unanswered",
                                 vetoed=["t::a — wrong type"]),
            byjudge=self._routing("byjudge", [_ref("t", "a")], status="unanswered",
                                   judge_note="none of these"),
        )
        got = {s.field: s.abstained_by for s in score(plan, {"byveto": [], "byjudge": []})}
        self.assertEqual(got, {"byveto": "veto", "byjudge": "judge"})

    def test_answering_an_unanswerable_field_counts_as_wrong(self):
        plan = _plan(f=self._routing("f", [_ref("t", "a")]))
        numbers = report(score(plan, {"f": []}), Console(width=100), 5)
        self.assertEqual(numbers["over_answered"], 1.0)
        self.assertEqual(numbers["accuracy"], 0.0)

    def test_abstaining_on_an_unanswerable_field_is_correct(self):
        plan = _plan(f=self._routing("f", [], status="unanswered"))
        numbers = report(score(plan, {"f": []}), Console(width=100), 5)
        self.assertEqual(numbers["over_answered"], 0.0)
        self.assertEqual(numbers["accuracy"], 1.0)

    def test_unlabeled_fields_are_skipped(self):
        plan = _plan(a=self._routing("a", [_ref("t", "a")]),
                     b=self._routing("b", [_ref("t", "b")]))
        self.assertEqual([s.field for s in score(plan, {"a": ["t::a"]})], ["a"])

    def test_risk_coverage_shrinks_as_the_threshold_rises(self):
        plan = _plan(
            lo=self._routing("lo", [_ref("t", "a", score_=1.0)]),
            hi=self._routing("hi", [_ref("t", "b", score_=9.0)]),
        )
        curve = risk_coverage(score(plan, {"lo": ["t::a"], "hi": ["t::b"]}), "bm25")
        coverages = [c for _, c, _ in curve]
        self.assertEqual(coverages, sorted(coverages, reverse=True))


class SheetSafetyTest(unittest.TestCase):
    def test_a_sheet_with_answers_is_recognised_as_labeled(self):
        """Hours of hand-labeling must not be destroyed by rerunning the generator."""
        directory = Path(tempfile.mkdtemp())
        blank, filled = directory / "blank.csv", directory / "filled.csv"
        for path, answer in ((blank, ""), (filled, UNANSWERABLE)):
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["field", "answer"])
                writer.writeheader()
                writer.writerow({"field": "f", "answer": answer})
        self.assertFalse(has_labels(blank))
        self.assertTrue(has_labels(filled))
        self.assertFalse(has_labels(directory / "absent.csv"))

    def test_none_loads_as_an_empty_answer_list(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "labels.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["field", "answer"])
            writer.writeheader()
            writer.writerows([
                {"field": "none", "answer": UNANSWERABLE},
                {"field": "some", "answer": "t::a|t::b"},
                {"field": "blank", "answer": "  "},
            ])
        labels = load_labels(path)
        self.assertEqual(labels["none"], [])
        self.assertEqual(labels["some"], ["t::a", "t::b"])
        self.assertNotIn("blank", labels)      # unlabeled, not unanswerable


if __name__ == "__main__":
    unittest.main()
