"""Tests for the column matcher (src/router/column_matcher.py).

Merging, splitting, and what the referee refuses to believe. Every test drives a stub
``invoke``; no model is contacted.
"""

import json
import os
import sys
import unittest
from typing import List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.router.catalog import ResolvedColumn
from src.router.column_matcher import (
    ColumnMatcher,
    LLMColumnMatcher,
    group_card,
    merge_columns,
    split_cards,
)
from src.router.judge import Verdict
from src.router.schema import FieldSpec


def _column(resource, name, description=None, units=None, value_range=None):
    return ResolvedColumn(resource=resource, name=name, dtype="float64",
                          description=description, units=units, value_range=value_range)


def _field(path):
    return FieldSpec(path=path, description=f"the {path}", type="str", required=False)


def _scripted(reply):
    prompts: List[str] = []

    def invoke(prompt):
        prompts.append(prompt)
        return reply(prompt) if callable(reply) else reply

    invoke.prompts = prompts
    return invoke


CARDS = [{"ref": "t::a", "kind": "column", "meaning": "fish mass"},
         {"ref": "t::b", "kind": "column", "meaning": "tank id"}]


class MergeTest(unittest.TestCase):
    def test_same_meaning_and_units_merge_across_tables(self):
        groups = merge_columns([
            _column("growth", "pH", "Acclimation pH treatment level"),
            _column("swim", "pH", "acclimation  pH treatment level"),
            _column("swim", "ucrit", "Critical swimming speed", "cm s-1"),
        ])
        self.assertEqual([len(g.members) for g in groups], [2, 1])
        self.assertEqual(groups[0].ref, "growth::pH")

    def test_different_units_do_not_merge(self):
        groups = merge_columns([
            _column("a", "mass", "fish mass", "g"),
            _column("b", "masskg", "fish mass", "kg"),
        ])
        self.assertEqual(len(groups), 2)

    def test_undescribed_columns_never_merge(self):
        """Two columns nobody described share only their silence."""
        groups = merge_columns([_column("a", "x"), _column("b", "x")])
        self.assertEqual(len(groups), 2)

    def test_a_merged_card_names_every_table_and_the_combined_range(self):
        group = merge_columns([
            _column("growth", "pH", "pH level", value_range=(4.0, 7.0)),
            _column("swim", "pH", "pH level", value_range=(4.0, 7.2)),
        ])[0]
        card = group_card(group)
        self.assertEqual(card["table"], ["growth", "swim"])
        self.assertEqual(card["column"], "pH")
        self.assertEqual(card["value_range"], "4 to 7.2")

    def test_the_card_shows_units_and_drops_what_is_unknown(self):
        """Units and range are what a name alone cannot convey."""
        card = group_card(merge_columns([_column("g", "days", "acclimation", "days")])[0])
        self.assertEqual(card["units"], "days")
        self.assertNotIn("value_range", card)


class SplitTest(unittest.TestCase):
    def test_a_small_catalog_is_one_slice(self):
        self.assertEqual(len(split_cards(CARDS, 10_000)), 1)

    def test_a_large_catalog_splits_and_every_slice_carries_the_tools(self):
        tool = {"ref": "tool::count", "kind": "tool", "computes": "rows"}
        slices = split_cards([tool] + CARDS, max_chars=60)
        self.assertEqual(len(slices), 2)
        self.assertTrue(all(s[0] == tool for s in slices))

    def test_splitting_issues_one_call_per_slice_and_keeps_the_strongest_pick(self):
        def reply(prompt):
            if "t::b" in prompt:
                return json.dumps({"one": {"choice": "t::b", "confidence": "high"}})
            return json.dumps({"one": {"choice": "t::a", "confidence": "low"}})

        invoke = _scripted(reply)
        verdicts = LLMColumnMatcher(invoke, max_chars=60).match_many(
            requests=[([_field("one")], CARDS)]
        )
        self.assertEqual(len(invoke.prompts), 2)
        self.assertEqual(verdicts["one"].choice, "t::b")

    def test_a_pick_in_one_slice_beats_an_abstention_in_another(self):
        def reply(prompt):
            if "t::a" in prompt:
                return json.dumps({"one": {"choice": "t::a", "confidence": "low"}})
            return json.dumps({"one": {"choice": None}})

        verdicts = LLMColumnMatcher(_scripted(reply), max_chars=60).match_many(
            requests=[([_field("one")], CARDS)]
        )
        self.assertEqual(verdicts["one"].choice, "t::a")


class FieldGroupingTest(unittest.TestCase):
    def _fields(self):
        return [_field(p) for p in ("one", "two", "three")]

    def test_fields_sharing_a_catalog_share_a_call(self):
        invoke = _scripted(json.dumps({}))
        LLMColumnMatcher(invoke).match_many(requests=[(self._fields(), CARDS)])
        self.assertEqual(len(invoke.prompts), 1)

    def test_batching_off_is_one_call_per_field(self):
        invoke = _scripted(json.dumps({}))
        LLMColumnMatcher(invoke, batch=False).match_many(requests=[(self._fields(), CARDS)])
        self.assertEqual(len(invoke.prompts), 3)

    def test_the_field_cap_splits_a_group(self):
        invoke = _scripted(json.dumps({}))
        LLMColumnMatcher(invoke, max_fields=2).match_many(requests=[(self._fields(), CARDS)])
        self.assertEqual(len(invoke.prompts), 2)

    def test_a_split_is_even_rather_than_filling_the_first_call(self):
        from src.router.judge import in_groups
        self.assertEqual([len(g) for g in in_groups(list(range(27)), 20)], [14, 13])
        self.assertEqual([len(g) for g in in_groups(list(range(20)), 20)], [20])
        self.assertEqual(in_groups([], 20), [])

    def test_concurrent_dispatch_returns_every_verdict(self):
        verdicts = LLMColumnMatcher(_scripted("{}"), batch=False, max_workers=4).match_many(
            requests=[(self._fields(), CARDS)]
        )
        self.assertEqual(set(verdicts), {"one", "two", "three"})

    def test_the_catalog_precedes_the_fields_for_prefix_caching(self):
        invoke = _scripted("{}")
        LLMColumnMatcher(invoke).match(fields=[_field("one")], cards=CARDS)
        self.assertLess(invoke.prompts[0].index("CATALOG:"), invoke.prompts[0].index("FIELDS:"))

    def test_the_default_entrypoint_loops_match(self):
        class Single(ColumnMatcher):
            def __init__(self):
                self.seen = []

            def match(self, *, fields, cards):
                self.seen.append([f.path for f in fields])
                return {f.path: Verdict(choice=None) for f in fields}

        matcher = Single()
        matcher.match_many(requests=[([_field("one")], CARDS), ([_field("two")], CARDS)])
        self.assertEqual(matcher.seen, [["one"], ["two"]])


class RefereeTest(unittest.TestCase):
    """What the code refuses to believe, regardless of how confident the model is."""

    def _verdict(self, reply):
        return LLMColumnMatcher(_scripted(reply)).match(fields=[_field("f")], cards=CARDS)["f"]

    def test_a_ref_that_was_not_shown_is_discarded(self):
        v = self._verdict(json.dumps({"f": {"choice": "t::invented", "confidence": "high"}}))
        self.assertTrue(v.abstained)
        self.assertIn("not in the catalog shown", v.because)

    def test_a_pick_keeps_its_confidence_and_needs_no_quote(self):
        v = self._verdict(json.dumps({"f": {"choice": "t::a", "confidence": "high"}}))
        self.assertEqual(v.choice, "t::a")
        self.assertEqual(v.confidence, "high")
        self.assertIsNone(v.grounded)

    def test_null_choice_is_a_first_class_abstention(self):
        v = self._verdict(json.dumps({"f": {"choice": None, "because": "nothing measures it"}}))
        self.assertTrue(v.abstained)
        self.assertEqual(v.because, "nothing measures it")

    def test_silence_about_a_field_is_not_a_pick(self):
        self.assertTrue(self._verdict(json.dumps({"other": {"choice": "t::a"}})).abstained)

    def test_garbled_output_abstains_rather_than_crashes(self):
        self.assertTrue(self._verdict("I think it's probably column A?").abstained)

    def test_a_raising_model_abstains_rather_than_crashes(self):
        def boom(_prompt):
            raise RuntimeError("connection reset")

        verdict = LLMColumnMatcher(boom).match(fields=[_field("f")], cards=CARDS)["f"]
        self.assertTrue(verdict.abstained)

    def test_an_identical_call_is_asked_once(self):
        invoke = _scripted("{}")
        matcher = LLMColumnMatcher(invoke)
        for _ in range(3):
            matcher.match(fields=[_field("f")], cards=CARDS)
        self.assertEqual(len(invoke.prompts), 1)


if __name__ == "__main__":
    unittest.main()
