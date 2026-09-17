"""Tests for the tool matcher (src/router/tool_matcher.py).

Cards, splitting, the referee, and the answer cache. Every test drives a stub
``invoke``; no model is contacted.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.tools  # noqa: F401 — registers the tools
from src.router.schema import FieldSpec
from src.router.tool_matcher import LLMToolMatcher, tool_card
from src.tools.tabular import temporal
from src.tools.universal import get_item_count


def _field(path):
    return FieldSpec(path=path, description=f"the {path}", type="str", required=False)


def _scripted(reply):
    prompts: List[str] = []

    def invoke(prompt):
        prompts.append(prompt)
        return reply(prompt) if callable(reply) else reply

    invoke.prompts = prompts
    return invoke


CARDS = [{"ref": "tool::get_item_count", "computes": "the number of rows"}]
COUNTED = json.dumps({"n": {"choice": "tool::get_item_count", "confidence": "high"}})


class CardTest(unittest.TestCase):
    def test_a_tool_card_says_what_it_computes(self):
        card = tool_card(get_item_count)
        self.assertEqual(card["ref"], "tool::get_item_count")
        self.assertIn("number of items", card["computes"])
        self.assertNotIn("needs columns", card)

    def test_a_tool_needing_columns_says_what_they_must_hold(self):
        card = tool_card(temporal.get_temporal_extent)
        self.assertEqual(list(card["needs columns"]), ["time_column"])


class MatchTest(unittest.TestCase):
    def test_a_pick_names_a_shown_tool(self):
        verdicts = LLMToolMatcher(_scripted(COUNTED)).match(fields=[_field("n")], cards=CARDS)
        self.assertEqual(verdicts["n"].choice, "tool::get_item_count")
        self.assertEqual(verdicts["n"].confidence, "high")

    def test_a_tool_that_was_not_shown_is_discarded(self):
        reply = json.dumps({"n": {"choice": "tool::invented", "confidence": "high"}})
        verdict = LLMToolMatcher(_scripted(reply)).match(fields=[_field("n")], cards=CARDS)["n"]
        self.assertTrue(verdict.abstained)

    def test_null_is_an_abstention(self):
        reply = json.dumps({"n": {"choice": None, "because": "a name is not computed"}})
        verdict = LLMToolMatcher(_scripted(reply)).match(fields=[_field("n")], cards=CARDS)["n"]
        self.assertTrue(verdict.abstained)
        self.assertEqual(verdict.because, "a name is not computed")

    def test_fields_are_split_evenly_and_tools_precede_fields(self):
        invoke = _scripted("{}")
        fields = [_field(f"f{i}") for i in range(5)]
        verdicts = LLMToolMatcher(invoke, max_fields=2).match(fields=fields, cards=CARDS)
        self.assertEqual(len(invoke.prompts), 3)
        self.assertEqual(set(verdicts), {f.path for f in fields})
        self.assertLess(invoke.prompts[0].index("TOOLS:"), invoke.prompts[0].index("FIELDS:"))

    def test_no_tools_means_no_call(self):
        invoke = _scripted(COUNTED)
        verdict = LLMToolMatcher(invoke).match(fields=[_field("n")], cards=[])["n"]
        self.assertTrue(verdict.abstained)
        self.assertEqual(invoke.prompts, [])


class CacheTest(unittest.TestCase):
    """The answer depends on the schema, the tools and the model — not on the bundle."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _match(self, invoke, model="m1"):
        matcher = LLMToolMatcher(invoke, cache_dir=self.dir, model=model)
        return matcher.match(fields=[_field("n")], cards=CARDS)

    def test_a_second_matcher_reads_the_answer_from_disk(self):
        self._match(_scripted(COUNTED))
        again = _scripted("{}")
        verdicts = self._match(again)
        self.assertEqual(again.prompts, [])
        self.assertEqual(verdicts["n"].choice, "tool::get_item_count")

    def test_refresh_asks_again_and_replaces_the_saved_answer(self):
        self._match(_scripted(COUNTED))
        fresh = _scripted(json.dumps({"n": {"choice": None}}))
        matcher = LLMToolMatcher(fresh, cache_dir=self.dir, model="m1", refresh=True)
        self.assertTrue(matcher.match(fields=[_field("n")], cards=CARDS)["n"].abstained)
        self.assertEqual(len(fresh.prompts), 1)
        later = _scripted(COUNTED)
        self.assertTrue(self._match(later)["n"].abstained)   # the refreshed answer was saved
        self.assertEqual(later.prompts, [])

    def test_another_model_asks_again(self):
        self._match(_scripted(COUNTED))
        other = _scripted(COUNTED)
        self._match(other, model="m2")
        self.assertEqual(len(other.prompts), 1)

    def test_a_failed_call_is_not_cached(self):
        def boom(_prompt):
            raise RuntimeError("connection reset")

        self.assertTrue(self._match(boom)["n"].abstained)
        retry = _scripted(COUNTED)
        self.assertEqual(self._match(retry)["n"].choice, "tool::get_item_count")
        self.assertEqual(len(retry.prompts), 1)


if __name__ == "__main__":
    unittest.main()
