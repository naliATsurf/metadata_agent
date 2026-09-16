"""Tests for the candidate judge — the layer-4b adjudicator (see src/router/judge.py).

The judge's job is mostly to *reject*, so most of what matters here is what happens
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
from src.router.judge import (
    LLMCandidateJudge,
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


class StubJudge(LLMCandidateJudge):
    """A judge whose every answer is scripted, recording the prompts it saw."""

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
        # A span collapses to its document: a judge cites a passage, not offsets.
        self.assertEqual(candidate_ref(span), "doc::readme")


class RefereeTest(unittest.TestCase):
    """What the code refuses to believe, regardless of how confident the model is."""

    field = FieldSpec(path="f", description="a field", type="str", required=True)
    cards = [{"ref": "t::a", "kind": "column", "meaning": "mass of the fish"}]

    def _verdict(self, reply) -> Verdict:
        return StubJudge(reply).choose(field=self.field, cards=self.cards)

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

        judge = LLMCandidateJudge(boom)
        self.assertTrue(judge.choose(field=self.field, cards=self.cards).abstained)

    def test_no_candidates_means_no_call(self):
        judge = StubJudge(_reply(choice="t::a"))
        self.assertTrue(judge.choose(field=self.field, cards=[]).abstained)
        self.assertEqual(judge.prompts, [])

    def test_one_call_per_distinct_field_and_candidate_set(self):
        judge = StubJudge(_reply(choice=None))
        for _ in range(3):
            judge.choose(field=self.field, cards=self.cards)
        self.assertEqual(len(judge.prompts), 1)


class BatchingTest(unittest.TestCase):
    """Grouping fields that share a candidate list into one round-trip."""

    cards_a = [{"ref": "t::a", "kind": "column", "meaning": "fish mass"},
               {"ref": "t::b", "kind": "column", "meaning": "tank id"}]
    cards_b = [{"ref": "t::c", "kind": "column", "meaning": "pH level"}]

    def _requests(self):
        def spec(path):
            return FieldSpec(path=path, description=f"the {path}", type="str",
                             required=False)
        return [
            (spec("one"), self.cards_a),
            (spec("two"), self.cards_a),     # identical set -> groups with "one"
            (spec("three"), self.cards_b),   # different set -> its own call
        ]

    def test_identical_candidate_sets_share_one_call(self):
        judge = StubJudge(_reply(one={"choice": None}, two={"choice": "t::a"}))
        judge.choose_many(requests=self._requests())
        # two calls: one for the {a,b} pair, one for the lone {c} field.
        self.assertEqual(len(judge.prompts), 2)

    def test_batching_off_is_one_call_per_field(self):
        judge = StubJudge(_reply(choice=None))
        judge._batch = False
        judge.choose_many(requests=self._requests())
        self.assertEqual(len(judge.prompts), 3)

    def test_a_grouped_answer_maps_back_to_each_field(self):
        judge = StubJudge(
            lambda prompt: _reply(
                one={"choice": "t::a", "quote": "fish mass", "confidence": "high"},
                two={"choice": None, "because": "not a mass"},
            )
            if "one" in prompt
            else _reply(three={"choice": None})
        )
        verdicts = judge.choose_many(requests=self._requests())
        self.assertEqual(verdicts["one"].choice, "t::a")
        self.assertEqual(verdicts["one"].confidence, "high")
        self.assertTrue(verdicts["two"].abstained)
        self.assertEqual(verdicts["two"].because, "not a mass")
        self.assertTrue(verdicts["three"].abstained)

    def test_a_field_missing_from_the_reply_abstains(self):
        """Silence about a field is not a pick — the group's other answers stand."""
        judge = StubJudge(_reply(one={"choice": "t::a", "quote": "fish mass"}))
        verdicts = judge.choose_many(requests=self._requests())
        self.assertEqual(verdicts["one"].choice, "t::a")
        self.assertTrue(verdicts["two"].abstained)

    def test_a_garbled_group_reply_abstains_every_field_in_it(self):
        judge = StubJudge("sorry, I can't tell")
        verdicts = judge.choose_many(requests=self._requests())
        self.assertTrue(all(v.abstained for v in verdicts.values()))

    def test_the_referee_still_applies_inside_a_group(self):
        judge = StubJudge(
            _reply(one={"choice": "t::invented", "confidence": "high"},
                   two={"choice": "t::a", "confidence": "high", "quote": "nowhere"})
        )
        verdicts = judge.choose_many(requests=self._requests())
        self.assertTrue(verdicts["one"].abstained)          # ref never offered
        self.assertEqual(verdicts["two"].confidence, "low")  # quote not locatable

    def test_concurrent_dispatch_returns_every_verdict(self):
        judge = StubJudge(_reply(one={"choice": None}, two={"choice": None},
                                   three={"choice": None}))
        judge._max_workers = 4
        verdicts = judge.choose_many(requests=self._requests())
        self.assertEqual(set(verdicts), {"one", "two", "three"})

    def test_the_default_batched_entrypoint_loops_choose(self):
        """A judge that implements only `choose` still works as a batched one."""
        from src.router.judge import CandidateJudge

        class Single(CandidateJudge):
            def __init__(self): self.seen = []
            def choose(self, *, field, cards):
                self.seen.append(field.path)
                return Verdict(choice=None)

        judge = Single()
        verdicts = judge.choose_many(requests=self._requests())
        self.assertEqual(judge.seen, ["one", "two", "three"])
        self.assertEqual(len(verdicts), 3)


class WeakerTest(unittest.TestCase):
    def test_two_hop_assurance_takes_the_weaker_grade(self):
        self.assertEqual(weaker("high", "low"), "low")
        self.assertEqual(weaker("low", "high"), "low")
        self.assertEqual(weaker("high", "high"), "high")
        self.assertEqual(weaker("medium", "none"), "none")


class RoutingIntegrationTest(unittest.TestCase):
    """The judge's effect on a real routing: promotion, abstention, fall-through."""

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
            judge=StubJudge(reply),
        )

    def test_without_a_judge_the_lexical_winner_stands(self):
        routing = route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5
        ).routings["duration_days"]
        self.assertEqual(routing.status, "routed")
        self.assertIsNone(routing.judge_choice)

    def test_the_judges_pick_becomes_rank_one(self):
        routing = self._route(
            _reply(choice="growth::days", confidence="high",
                   quote="Length of the acclimation period")
        ).routings["duration_days"]
        self.assertEqual(candidate_ref(routing.candidates[0]), "growth::days")
        self.assertEqual(routing.judge_choice, "growth::days")
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
        self.assertIsNone(routing.judge_choice)

    def test_rejecting_the_structured_tier_falls_through_to_documents(self):
        """A judge that dismisses lexical coincidences still gets to read the prose."""
        def reply(prompt: str) -> str:
            if "Foo Survey" not in prompt:
                return _reply(choice=None, because="no column holds a title")
            verdict = dict(choice="doc::doc", confidence="medium",
                           quote="The dataset title is Foo Survey")
            # The document tier offers whole passages, so one passage serves every
            # pending field and they are judged together — a batch answer is keyed
            # by field, where a single-field one is flat.
            if "FIELDS:" in prompt:
                return json.dumps({"title": verdict})
            return _reply(**verdict)

        routing = self._route(reply).routings["title"]
        self.assertEqual(routing.bucket, "document")
        self.assertEqual(routing.judge_choice, "doc::doc")

    def test_assurance_never_exceeds_the_judges_confidence(self):
        routing = self._route(
            _reply(choice="growth::days", confidence="low",
                   quote="Length of the acclimation period")
        ).routings["duration_days"]
        self.assertEqual(routing.assurance, "low")

    def test_the_card_shows_units_and_the_value_range(self):
        """The two things a name alone cannot convey, and the judge's only defence."""
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

    def test_the_veto_cuts_the_bait_before_the_judge_sees_it(self):
        """`condition` runs 0.97-1.11, so it cannot answer a field wanting whole days."""
        judge = StubJudge(_reply(choice=None))
        routing = route_fields(
            Meta, catalog=self.catalog, docs=[self.doc], k=5, judge=judge
        ).routings["duration_days"]
        self.assertTrue(any("condition" in reason for reason in routing.vetoed))
        offered = "".join(judge.prompts)
        self.assertNotIn("growth::condition", offered)
        self.assertIn("growth::days", offered)


if __name__ == "__main__":
    unittest.main()
