"""Tests for the layer-4b judges in a real routing, and what they share.

The judges' job is mostly to *reject*, so most of what matters here is what happens
when they say no. Every test drives a stub ``invoke``; no model is contacted. The
judges on their own are tested in test_tool_matcher.py, test_column_matcher.py and
test_passage_reader.py.
"""

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from typing import List, Optional

import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import thresholds
from src.context import create_context
from src.context.base_context import EvidenceRef
from src.router import resolve_bundle, resolve_catalog, route_fields
from src.router.column_matcher import LLMColumnMatcher
from src.router.judge import candidate_ref, contains, json_object, weaker
from src.router.passage_reader import LLMPassageReader
from src.router.tool_matcher import LLMToolMatcher
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

    def test_a_refused_field_lists_nothing_and_says_the_judges_refused_it(self):
        routing = self._route(_json()).routings["duration_days"]
        # What does not answer the field is no place to look for its value; the note is
        # what tells a refusal apart from an empty search.
        self.assertEqual(routing.candidates, [])
        self.assertIsNone(routing.judge_choice)
        self.assertTrue(routing.judge_note)

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
        self.assertEqual(routing.citations, [f"doc#{start}-{end}"])
        self.assertEqual(routing.judge_quotes, ["The dataset title is Foo Survey."])
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


class DocumentAnswersTest(unittest.TestCase):
    """A document routing lists the sentences that answer the field, and nothing else."""

    TEXT = (
        "The dataset title is Foo Survey.\n\n"
        "Fish were held for two weeks.\n\n"
        "Cite this dataset as Foo Survey.\n"
    )

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        path = os.path.join(self.dir, "readme.txt")
        with open(path, "w") as handle:
            handle.write(self.TEXT)
        self.doc = create_context(path, name="readme")

    def _title(self, reply, passage_chars=40):
        settings = replace(thresholds.current(), router_passage_max_chars=passage_chars)
        with thresholds.use(settings):
            plan = route_fields(Meta, docs=[self.doc], k=5, reader=LLMPassageReader(_scripted(reply)))
        return plan.routings["title"]

    def _span(self, sentence):
        start = self.TEXT.index(sentence)
        return (start, start + len(sentence))

    def test_every_passage_that_states_it_is_kept_and_the_others_dropped(self):
        def reply(prompt):
            if "Foo Survey" in prompt:
                sentence = next(s for s in ("The dataset title is Foo Survey.",
                                            "Cite this dataset as Foo Survey.") if s in prompt)
                return _json(title={"stated": True, "quotes": [sentence], "confidence": "high"})
            return _json(title={"stated": False})

        routing = self._title(reply)
        self.assertEqual(
            [c.locator for c in routing.candidates],
            [self._span("The dataset title is Foo Survey."),
             self._span("Cite this dataset as Foo Survey.")],
        )
        self.assertEqual(len(routing.citations), 2)
        self.assertTrue(all(routing.citations))

    def test_one_passage_stating_it_twice_gives_two_quotes(self):
        quotes = ["The dataset title is Foo Survey.", "Cite this dataset as Foo Survey."]
        routing = self._title(
            _json(title={"stated": True, "quotes": quotes, "confidence": "high"}),
            passage_chars=10_000,
        )
        self.assertEqual(routing.judge_quotes, quotes)
        self.assertEqual([c.locator for c in routing.candidates],
                         [self._span(q) for q in quotes])
        self.assertTrue(routing.judge_grounded)

    def test_a_located_answer_drops_a_passage_whose_quote_was_not_found(self):
        def reply(prompt):
            if "The dataset title" in prompt:
                return _json(title={"stated": True, "confidence": "low",
                                    "quotes": ["The dataset title is Foo Survey."]})
            if "Cite this" in prompt:
                return _json(title={"stated": True, "confidence": "high",
                                    "quotes": ["The title is Bar."]})
            return _json(title={"stated": False})

        routing = self._title(reply)
        self.assertEqual([c.locator for c in routing.candidates],
                         [self._span("The dataset title is Foo Survey.")])
        self.assertTrue(routing.judge_grounded)

    def test_an_answer_with_no_located_quote_keeps_its_passage_and_says_so(self):
        routing = self._title(
            _json(title={"stated": True, "quotes": ["The title is Bar."], "confidence": "high"}),
            passage_chars=10_000,
        )
        self.assertEqual(routing.candidates[0].locator, (0, len(self.TEXT.rstrip("\n"))))
        self.assertEqual(routing.citations, [None])
        self.assertFalse(routing.judge_grounded)
        self.assertEqual(routing.assurance, "low")


class ReadAllPassagesTest(unittest.TestCase):
    """With a reader, BM25 does not choose passages — unless there are too many."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        path = os.path.join(self.dir, "readme.txt")
        with open(path, "w") as handle:
            # Two paragraphs, packed apart below; only the first shares a word with
            # the title field's description.
            handle.write("The dataset title is Foo Survey.\n\nFish were held for two weeks.\n")
        self.doc = create_context(path, name="readme")

    def _titles_read(self, **limits):
        read = _scripted("{}")
        settings = replace(thresholds.current(), router_passage_max_chars=40, **limits)
        with thresholds.use(settings):
            plan = route_fields(Meta, docs=[self.doc], k=5, reader=LLMPassageReader(read))
        return [p for p in read.prompts if "- title" in p], plan

    def test_every_passage_is_read_for_every_unanswered_field(self):
        prompts, plan = self._titles_read()
        self.assertEqual(len(prompts), 2)
        self.assertTrue(plan.judged)

    def test_bm25_chooses_passages_only_past_the_read_all_limit(self):
        prompts, _ = self._titles_read(router_read_all_max_passages=1)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Foo Survey", prompts[0])


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
        self.assertEqual(
            [candidate_ref(c) for c in routing.candidates], ["growth::days", "swim::days"]
        )

    def test_the_plan_records_the_catalog_the_matcher_saw_once(self):
        match = _scripted("{}")
        plan = route_fields(Meta, catalog=self.catalog, k=5, matcher=LLMColumnMatcher(match))
        self.assertIn("growth::days", plan.catalog_shown)
        self.assertNotIn("swim::days", plan.catalog_shown)   # merged into growth::days
        # Tools are the tool matcher's; without one, none are considered.
        self.assertFalse(any(ref.startswith("tool::") for ref in plan.catalog_shown))
        self.assertEqual(plan.tools_shown, [])
        self.assertTrue(plan.judged)


class Tooled(BaseModel):
    record_count: Optional[str] = Field(default=None, description="number of records collected")
    sampling_period: Optional[str] = Field(
        default=None, description="period over which the data were collected"
    )
    species: Optional[str] = Field(default=None, description="species studied")
    tank: Optional[str] = Field(default=None, description="tank the fish were held in")


class ToolBindingTest(unittest.TestCase):
    """The tool matcher picks an operation; the column matcher picks what it runs on."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({
            "date": ["2021-05-01", "2021-05-02", "2021-05-03"],
            "day": [1, 2, 3],
            "species": ["Salmo trutta"] * 3,
            "tank": [1, 2, 2],
        }).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        pd.DataFrame({
            "variable": ["date", "day", "species", "tank"],
            "description": ["Sampling date", "Day of the trial", "Species name", "Tank number"],
            "units": ["", "days", "", ""],
        }).to_csv(os.path.join(self.dir, "cb.csv"), index=False)
        tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        self.catalog = resolve_catalog(tab, sources=[cb])

    def tearDown(self):
        clear_registry()

    def _route(self, tools, columns):
        self.tools = _scripted(json.dumps(tools))
        self.columns = _scripted(json.dumps(columns))
        return route_fields(
            Tooled, catalog=self.catalog, k=5,
            tool_matcher=LLMToolMatcher(self.tools), matcher=LLMColumnMatcher(self.columns),
        )

    def _counted(self, **columns):
        return self._route(
            {"record_count": {"choice": "tool::get_item_count", "confidence": "high"}},
            {"record_count[resource]": {"choice": "table::obs", "confidence": "high"}, **columns},
        )

    def _dated(self, time_column, **columns):
        return self._route(
            {"sampling_period": {"choice": "tool::get_temporal_extent", "confidence": "high"}},
            {"sampling_period[time_column]": time_column, **columns},
        )

    def test_a_tool_runs_on_the_table_the_column_matcher_chose(self):
        plan = self._counted()
        routing = plan.routings["record_count"]
        self.assertEqual(routing.bucket, "tool")
        self.assertEqual(candidate_ref(routing.candidates[0]), "tool::get_item_count")
        self.assertEqual(routing.candidates[0].resource, "obs")
        self.assertEqual(routing.tool_arguments, [{"resource": "obs"}])
        self.assertEqual(routing.tool_choice, "tool::get_item_count")
        self.assertIn("tool::get_item_count", plan.tools_shown)
        prompt = self.columns.prompts[0]
        self.assertIn("- record_count[resource] (table)", prompt)
        self.assertIn('"ref": "table::obs"', prompt)

    def test_the_tool_matcher_sees_no_data(self):
        self._counted()
        self.assertNotIn("Salmo trutta", self.tools.prompts[0])
        self.assertNotIn("obs::", self.tools.prompts[0])

    def test_tables_are_shown_only_when_a_tool_asks_for_one(self):
        self._route({}, {})
        self.assertNotIn("table::", self.columns.prompts[0])

    def test_a_column_argument_is_bound_to_its_table(self):
        routing = self._dated({"choice": "obs::date", "confidence": "high"}).routings[
            "sampling_period"
        ]
        self.assertEqual(routing.bucket, "tool")
        self.assertEqual(routing.tool_arguments, [{"resource": "obs", "time_column": "date"}])
        self.assertEqual(routing.mismatches, [])
        self.assertIn("- sampling_period[time_column] (column)", self.columns.prompts[0])

    def test_a_column_that_does_not_fit_its_argument_lowers_confidence(self):
        routing = self._dated({"choice": "obs::day", "confidence": "high"}).routings[
            "sampling_period"
        ]
        self.assertEqual(routing.bucket, "tool")
        self.assertEqual(routing.assurance, "low")
        self.assertTrue(any("time_column needs dates" in m for m in routing.mismatches))

    def test_a_tool_missing_an_argument_is_dropped(self):
        routing = self._dated({"choice": None}).routings["sampling_period"]
        self.assertNotEqual(routing.bucket, "tool")
        self.assertEqual(routing.tool_arguments, [])
        self.assertEqual(routing.tool_choice, "tool::get_temporal_extent")
        self.assertIn("not run: no column chosen for time_column", routing.tool_note)

    def test_a_table_named_for_a_column_argument_does_not_bind_it(self):
        routing = self._dated({"choice": "obs::date"}).routings["sampling_period"]
        self.assertEqual(routing.bucket, "tool")
        routing = self._dated({"choice": "table::obs"}).routings["sampling_period"]
        self.assertNotEqual(routing.bucket, "tool")

    def test_the_fields_own_pick_agreeing_with_the_tool_is_one_routing(self):
        routing = self._dated(
            {"choice": "obs::date", "confidence": "high"},
            sampling_period={"choice": "obs::date", "confidence": "high"},
        ).routings["sampling_period"]
        self.assertEqual([c.kind for c in routing.candidates], ["tool"])

    def test_a_different_column_leads_and_caps_confidence(self):
        routing = self._counted(
            record_count={"choice": "obs::day", "confidence": "high"}
        ).routings["record_count"]
        self.assertEqual(
            [candidate_ref(c) for c in routing.candidates], ["obs::day", "tool::get_item_count"]
        )
        self.assertEqual(routing.bucket, "column")
        self.assertEqual(routing.assurance, "medium")
        self.assertEqual(routing.tool_arguments, [{"resource": "obs"}])

    def test_a_tool_matcher_needs_a_column_matcher(self):
        with self.assertRaises(ValueError):
            route_fields(Tooled, catalog=self.catalog, tool_matcher=LLMToolMatcher(_scripted("{}")))

    def test_without_judges_a_tool_needing_columns_is_never_a_candidate(self):
        class Extent(BaseModel):
            extent: Optional[str] = Field(
                default=None, description="temporal extent start end duration timestamp column"
            )

        routing = route_fields(Extent, catalog=self.catalog, k=5).routings["extent"]
        self.assertNotIn("tool::get_temporal_extent", [candidate_ref(c) for c in routing.candidates])

    def test_a_varying_column_is_noted_without_lowering_confidence(self):
        plan = self._route({}, {
            "species": {"choice": "obs::species", "confidence": "high"},
            "tank": {"choice": "obs::tank", "confidence": "high"},
        })
        species, tank = plan.routings["species"], plan.routings["tank"]
        self.assertEqual(species.varies, [])                 # one value down every row
        self.assertEqual(tank.varies, ["obs::tank — tank holds 2 different values"])
        self.assertEqual(tank.assurance, species.assurance)
        self.assertIn('"distinct_values": [\n      "Salmo trutta"\n    ]', self.columns.prompts[0])


if __name__ == "__main__":
    unittest.main()
