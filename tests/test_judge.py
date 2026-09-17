"""Tests for the layer-4b judges in a real routing, and what they share.

The judges' job is mostly to *reject*, so most of what matters here is what happens
when they say no. Every test drives a stub ``invoke``; no model is contacted. The
matcher and reader on their own are tested in test_column_matcher.py and
test_passage_reader.py.
"""

import json
import os
import sys
import tempfile
import unittest
from typing import List, Optional

import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.context.base_context import EvidenceRef
from src.router import resolve_bundle, resolve_catalog, route_fields
from src.router.column_matcher import LLMColumnMatcher
from src.router.judge import candidate_ref, contains, json_object, weaker
from src.router.passage_reader import LLMPassageReader
from src.tools.base import clear_registry


class Meta(BaseModel):
    duration_days: Optional[int] = Field(
        default=None, description="duration period in days of the condition"
    )
    title: str = Field(description="The title of the dataset")


def _scripted(reply):
    """An ``invoke`` returning ``reply`` (or ``reply(prompt)``), recording each prompt."""
    prompts: List[str] = []

    def invoke(prompt: str) -> str:
        prompts.append(prompt)
        return reply(prompt) if callable(reply) else reply

    invoke.prompts = prompts
    return invoke


def _json(**payload) -> str:
    return json.dumps(payload)


class RefTest(unittest.TestCase):
    def test_ref_scheme_matches_the_label_vocabulary(self):
        column = EvidenceRef(resource="growth", locator="pH", kind="computed_column",
                             snippet="", score=1.0)
        tool = EvidenceRef(resource="", locator="get_item_count", kind="tool",
                           snippet="", score=1.0)
        span = EvidenceRef(resource="readme", locator=(0, 40), kind="quoted_span",
                           snippet="", score=1.0)
        self.assertEqual(candidate_ref(column), "growth::pH")
        self.assertEqual(candidate_ref(tool), "tool::get_item_count")
        # A span collapses to its document: a judge cites a passage, not offsets.
        self.assertEqual(candidate_ref(span), "doc::readme")


class SharedTest(unittest.TestCase):
    def test_two_hop_assurance_takes_the_weaker_grade(self):
        self.assertEqual(weaker("high", "low"), "low")
        self.assertEqual(weaker("low", "high"), "low")
        self.assertEqual(weaker("high", "high"), "high")
        self.assertEqual(weaker("medium", "none"), "none")

    def test_containment_tolerates_reflowed_whitespace_and_case(self):
        self.assertTrue(contains("held at\n21.5 C", "HELD AT 21.5 c"))
        self.assertFalse(contains("held at 21.5 C", ""))

    def test_a_json_object_is_found_inside_stray_prose(self):
        self.assertEqual(json_object('Sure: {"a": 1} hope that helps'), {"a": 1})
        self.assertIsNone(json_object("no json here"))


class RoutingIntegrationTest(unittest.TestCase):
    """The judges' effect on a real routing: promotion, abstention, fall-through."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # `condition` is the lexical bait: it wins "duration ... of the condition"
        # on one word while measuring something unitless and unrelated.
        pd.DataFrame({"condition": [1.04, 0.97, 1.11], "days": [14, 14, 21]}).to_csv(
            os.path.join(self.dir, "growth.csv"), index=False
        )
        pd.DataFrame({
            "variable": ["condition", "days"],
            "description": ["Fulton's condition factor", "Length of the acclimation period"],
            "units": ["", "days"],
        }).to_csv(os.path.join(self.dir, "cb.csv"), index=False)
        with open(os.path.join(self.dir, "doc.md"), "w") as handle:
            handle.write("# Dataset\n\nThe dataset title is Foo Survey.\n")

        self.tab = create_context(os.path.join(self.dir, "growth.csv"), name="growth")
        self.cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        self.doc = create_context(os.path.join(self.dir, "doc.md"), name="doc")
        self.catalog = resolve_catalog(self.tab, sources=[self.cb])

    def tearDown(self):
        clear_registry()

    def _route(self, match_reply, read_reply=_json(title={"stated": False})):
        self.match = _scripted(match_reply)
        self.read = _scripted(read_reply)
        return route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5,
            matcher=LLMColumnMatcher(self.match), reader=LLMPassageReader(self.read),
        )

    def test_without_judges_the_lexical_winner_stands(self):
        routing = route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5
        ).routings["duration_days"]
        self.assertEqual(routing.status, "routed")
        self.assertIsNone(routing.judge_choice)

    def test_the_matchers_pick_becomes_rank_one(self):
        routing = self._route(
            _json(duration_days={"choice": "growth::days", "confidence": "high"})
        ).routings["duration_days"]
        self.assertEqual(candidate_ref(routing.candidates[0]), "growth::days")
        self.assertEqual(routing.judge_choice, "growth::days")
        # Everything downstream reads candidates[0], so the pick leading is what makes
        # the bucket and the task's resource follow a judgement instead of a BM25 tie.
        self.assertEqual(routing.bucket, "column")

    def test_every_field_is_matched_against_one_catalog_in_one_call(self):
        """Matching is per catalog, not per field — and type never splits the catalog."""
        self._route(_json())
        self.assertEqual(len(self.match.prompts), 1)
        self.assertIn("- duration_days", self.match.prompts[0])
        self.assertIn("- title", self.match.prompts[0])

    def test_rejecting_everything_marks_the_field_unanswered(self):
        plan = self._route(_json(duration_days={"choice": None, "because": "no duration"}))
        routing = plan.routings["duration_days"]
        self.assertEqual(routing.status, "unanswered")
        self.assertEqual(routing.bucket, "unanswered")
        self.assertIn("duration_days", plan.unanswered())

    def test_a_rejected_set_is_still_recorded(self):
        routing = self._route(_json()).routings["duration_days"]
        # Keeping the candidates is the record of what was considered and refused;
        # an empty list would make an abstention indistinguishable from no retrieval.
        self.assertTrue(routing.candidates)
        self.assertIsNone(routing.judge_choice)

    def test_rejecting_the_structured_tier_falls_through_to_documents(self):
        """A matcher that dismisses the columns still leaves the prose to be read."""
        routing = self._route(
            _json(title={"choice": None, "because": "no column holds a title"}),
            _json(title={"stated": True, "quote": "The dataset title is Foo Survey.",
                         "confidence": "medium"}),
        ).routings["title"]
        self.assertEqual(routing.bucket, "document")
        self.assertEqual(routing.judge_choice, "doc::doc")
        self.assertTrue(routing.judge_grounded)
        # The located quote narrows the routing from the passage to the sentence.
        start, end = routing.candidates[0].locator
        self.assertEqual(routing.citation, f"doc#{start}-{end}")
        self.assertEqual(routing.candidates[0].snippet, "The dataset title is Foo Survey.")

    def test_a_passage_is_read_once_for_every_field_that_retrieved_it(self):
        self._route(_json())
        self.assertEqual(len(self.read.prompts), 1)

    def test_assurance_never_exceeds_the_judges_confidence(self):
        routing = self._route(
            _json(duration_days={"choice": "growth::days", "confidence": "low"})
        ).routings["duration_days"]
        self.assertEqual(routing.assurance, "low")

    def test_a_type_mismatch_is_shown_to_the_matcher_not_hidden_from_it(self):
        """`condition` runs 0.97-1.11: a poor fit for whole days, but still on the card."""
        self._route(_json())
        self.assertIn("growth::condition", self.match.prompts[0])
        self.assertIn("0.97 to 1.11", self.match.prompts[0])

    def test_a_pick_whose_type_does_not_fit_stays_but_drops_to_low(self):
        """A mismatch lowers confidence; it never decides relevance."""
        routing = self._route(
            _json(duration_days={"choice": "growth::condition", "confidence": "high"})
        ).routings["duration_days"]
        self.assertEqual(routing.status, "routed")
        self.assertEqual(candidate_ref(routing.candidates[0]), "growth::condition")
        self.assertEqual(routing.assurance, "low")
        self.assertTrue(any("condition" in reason for reason in routing.mismatches))

    def test_without_judges_a_mismatched_winner_is_kept_at_low(self):
        class Counted(BaseModel):
            condition_count: Optional[int] = Field(default=None, description="condition")

        routing = route_fields(Counted, catalog=self.catalog, k=5).routings["condition_count"]
        # `condition` wins on the word, and holds fractions where a count is wanted.
        self.assertEqual(candidate_ref(routing.candidates[0]), "growth::condition")
        self.assertEqual(routing.status, "routed")
        self.assertEqual(routing.assurance, "low")
        self.assertTrue(routing.mismatches)


class MergedColumnRoutingTest(unittest.TestCase):
    """A column repeated across tables is matched once and routed to every table."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in ("growth", "swim"):
            pd.DataFrame({"days": [14, 21, 28]}).to_csv(
                os.path.join(self.dir, f"{name}.csv"), index=False
            )
        pd.DataFrame({
            "variable": ["days"],
            "description": ["Length of the acclimation period"],
            "units": ["days"],
        }).to_csv(os.path.join(self.dir, "cb.csv"), index=False)
        tables = [create_context(os.path.join(self.dir, f"{n}.csv"), name=n)
                  for n in ("growth", "swim")]
        cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        self.catalog = resolve_bundle(tables, sources=[cb])

    def tearDown(self):
        clear_registry()

    def test_one_card_fans_out_to_every_table(self):
        match = _scripted(_json(duration_days={"choice": "growth::days",
                                               "confidence": "high"}))
        routing = route_fields(
            Meta, catalog=self.catalog, k=5, matcher=LLMColumnMatcher(match),
        ).routings["duration_days"]
        self.assertEqual(match.prompts[0].count('"ref": "growth::days"'), 1)
        self.assertNotIn('"ref": "swim::days"', "".join(match.prompts))
        refs = [candidate_ref(c) for c in routing.candidates[:2]]
        self.assertEqual(refs, ["growth::days", "swim::days"])


if __name__ == "__main__":
    unittest.main()
