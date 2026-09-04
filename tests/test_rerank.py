"""Tests for the field reader — the layer-4b adjudicator (see src/router/rerank.py).

The reader's job is mostly to *reject*, so most of what matters here is what happens
when it says no, and what happens when it says something the code should not believe.
Every test drives a stub ``invoke``; no model is contacted.
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
from src.router import resolve_catalog, route_fields
from src.router.rerank import (
    LLMFieldReader,
    Verdict,
    candidate_ref,
    describe,
    weaker,
)
from src.router.schema import FieldSpec
from src.tools.base import clear_registry


class Meta(BaseModel):
    duration_days: Optional[int] = Field(
        default=None, description="duration period in days of the condition"
    )
    title: str = Field(description="The title of the dataset")


def _reply(**payload) -> str:
    return json.dumps(payload)


class StubReader(LLMFieldReader):
    """A reader whose every answer is scripted, recording the prompts it saw."""

    def __init__(self, replies):
        self.prompts: List[str] = []
        self._replies = replies

        def invoke(prompt: str) -> str:
            self.prompts.append(prompt)
            reply = self._replies
            return reply(prompt) if callable(reply) else reply

        super().__init__(invoke)


class RefTest(unittest.TestCase):
    def test_ref_scheme_matches_the_label_vocabulary(self):
        from src.context.base_context import EvidenceRef

        column = EvidenceRef(resource="growth", locator="pH", kind="computed_column",
                             snippet="", score=1.0)
        tool = EvidenceRef(resource="", locator="get_item_count", kind="tool",
                           snippet="", score=1.0)
        span = EvidenceRef(resource="readme", locator=(0, 40), kind="quoted_span",
                           snippet="", score=1.0)
        self.assertEqual(candidate_ref(column), "growth::pH")
        self.assertEqual(candidate_ref(tool), "tool::get_item_count")
        # A span collapses to its document: a reader cites a passage, not offsets.
        self.assertEqual(candidate_ref(span), "doc::readme")


class RefereeTest(unittest.TestCase):
    """What the code refuses to believe, regardless of how confident the model is."""

    field = FieldSpec(path="f", description="a field", type="str", required=True)
    cards = [{"ref": "t::a", "kind": "column", "meaning": "mass of the fish"}]

    def _verdict(self, reply) -> Verdict:
        return StubReader(reply).choose(field=self.field, cards=self.cards)

    def test_a_ref_that_was_not_offered_is_discarded(self):
        v = self._verdict(_reply(choice="t::invented", confidence="high", quote="mass"))
        self.assertTrue(v.abstained)
        self.assertIn("not among the candidates", v.because)

    def test_an_unlocatable_quote_caps_confidence_at_low(self):
        v = self._verdict(_reply(choice="t::a", confidence="high", quote="length in cm"))
        self.assertEqual(v.choice, "t::a")
        self.assertFalse(v.grounded)
        self.assertEqual(v.confidence, "low")

    def test_a_located_quote_keeps_its_confidence(self):
        v = self._verdict(_reply(choice="t::a", confidence="high", quote="mass of the FISH"))
        self.assertTrue(v.grounded)
        self.assertEqual(v.confidence, "high")

    def test_an_empty_quote_is_not_a_citation(self):
        v = self._verdict(_reply(choice="t::a", confidence="high", quote=""))
        self.assertFalse(v.grounded)
        self.assertEqual(v.confidence, "low")

    def test_null_choice_is_a_first_class_abstention(self):
        v = self._verdict(_reply(choice=None, because="nothing measures this"))
        self.assertTrue(v.abstained)
        self.assertEqual(v.because, "nothing measures this")

    def test_garbled_output_abstains_rather_than_crashes(self):
        self.assertTrue(self._verdict("I think it's probably column A?").abstained)

    def test_a_raising_model_abstains_rather_than_crashes(self):
        def boom(_prompt):
            raise RuntimeError("connection reset")

        reader = LLMFieldReader(boom)
        self.assertTrue(reader.choose(field=self.field, cards=self.cards).abstained)

    def test_no_candidates_means_no_call(self):
        reader = StubReader(_reply(choice="t::a"))
        self.assertTrue(reader.choose(field=self.field, cards=[]).abstained)
        self.assertEqual(reader.prompts, [])

    def test_one_call_per_distinct_field_and_candidate_set(self):
        reader = StubReader(_reply(choice=None))
        for _ in range(3):
            reader.choose(field=self.field, cards=self.cards)
        self.assertEqual(len(reader.prompts), 1)


class WeakerTest(unittest.TestCase):
    def test_two_hop_assurance_takes_the_weaker_grade(self):
        self.assertEqual(weaker("high", "low"), "low")
        self.assertEqual(weaker("low", "high"), "low")
        self.assertEqual(weaker("high", "high"), "high")
        self.assertEqual(weaker("medium", "none"), "none")


class RoutingIntegrationTest(unittest.TestCase):
    """The reader's effect on a real routing: promotion, abstention, fall-through."""

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

    def _route(self, reply):
        return route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5,
            reader=StubReader(reply),
        )

    def test_without_a_reader_the_lexical_winner_stands(self):
        routing = route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5
        ).routings["duration_days"]
        self.assertEqual(routing.status, "routed")
        self.assertIsNone(routing.reader_choice)

    def test_the_readers_pick_becomes_rank_one(self):
        routing = self._route(
            _reply(choice="growth::days", confidence="high",
                   quote="Length of the acclimation period")
        ).routings["duration_days"]
        self.assertEqual(candidate_ref(routing.candidates[0]), "growth::days")
        self.assertEqual(routing.reader_choice, "growth::days")
        # Everything downstream reads candidates[0], so promotion is what makes the
        # bucket and the task's resource follow a judgement instead of a BM25 tie.
        self.assertEqual(routing.bucket, "column")

    def test_rejecting_everything_marks_the_field_unanswered(self):
        plan = self._route(_reply(choice=None, because="no column holds a duration"))
        routing = plan.routings["duration_days"]
        self.assertEqual(routing.status, "unanswered")
        self.assertEqual(routing.bucket, "unanswered")
        self.assertIn("duration_days", plan.unanswered())

    def test_a_rejected_set_is_still_recorded(self):
        routing = self._route(_reply(choice=None)).routings["duration_days"]
        # Keeping the candidates is the record of what was considered and refused;
        # an empty list would make an abstention indistinguishable from no retrieval.
        self.assertTrue(routing.candidates)
        self.assertIsNone(routing.reader_choice)

    def test_rejecting_the_structured_tier_falls_through_to_documents(self):
        """A reader that dismisses lexical coincidences still gets to read the prose."""
        def reply(prompt: str) -> str:
            if "Foo Survey" in prompt:
                return _reply(choice="doc::doc", confidence="medium",
                              quote="The dataset title is Foo Survey")
            return _reply(choice=None, because="no column holds a title")

        routing = self._route(reply).routings["title"]
        self.assertEqual(routing.bucket, "document")
        self.assertEqual(routing.reader_choice, "doc::doc")

    def test_assurance_never_exceeds_the_readers_confidence(self):
        routing = self._route(
            _reply(choice="growth::days", confidence="low",
                   quote="Length of the acclimation period")
        ).routings["duration_days"]
        self.assertEqual(routing.assurance, "low")

    def test_the_card_shows_units_and_the_value_range(self):
        """The two things a name alone cannot convey, and the reader's only defence."""
        from src.context.base_context import EvidenceRef

        cards = {
            column.name: describe(
                EvidenceRef(resource=column.resource, locator=column.name,
                            kind="computed_column", snippet="", score=1.0),
                self.catalog,
            )
            for column in self.catalog.columns
        }
        self.assertEqual(cards["days"]["units"], "days")
        self.assertEqual(cards["condition"]["meaning"], "Fulton\'s condition factor")
        self.assertIn("value_range", cards["condition"])
        self.assertNotIn("units", cards["condition"])   # unitless: the key is absent

    def test_the_veto_cuts_the_bait_before_the_reader_sees_it(self):
        """`condition` runs 0.97-1.11, so it cannot answer a field wanting whole days."""
        reader = StubReader(_reply(choice=None))
        routing = route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5, reader=reader
        ).routings["duration_days"]
        self.assertTrue(any("condition" in reason for reason in routing.vetoed))
        offered = "".join(reader.prompts)
        self.assertNotIn("growth::condition", offered)
        self.assertIn("growth::days", offered)


if __name__ == "__main__":
    unittest.main()
