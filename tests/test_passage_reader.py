"""Tests for the passage reader (src/router/passage_reader.py).

Every test drives a stub ``invoke``; no model is contacted.
"""

import json
import os
import sys
import unittest
from typing import List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.router.judge import Verdict
from src.router.passage_reader import LLMPassageReader, PassageReader
from src.router.schema import FieldSpec

PASSAGE = {"ref": "doc::readme",
           "text": "Fish were held at 21.5 °C under a 12:12-h light–dark cycle."}


def _field(path):
    return FieldSpec(path=path, description=f"the {path}", type="str", required=False)


def _scripted(reply):
    prompts: List[str] = []

    def invoke(prompt):
        prompts.append(prompt)
        return reply(prompt) if callable(reply) else reply

    invoke.prompts = prompts
    return invoke


class RefereeTest(unittest.TestCase):
    """What the code refuses to believe, regardless of how confident the model is."""

    def _verdict(self, **answer) -> Verdict:
        reply = json.dumps({"f": answer})
        return LLMPassageReader(_scripted(reply)).read(fields=[_field("f")], passage=PASSAGE)["f"]

    def test_a_located_quote_keeps_its_confidence(self):
        v = self._verdict(stated=True, quotes=["held at 21.5 °C"], confidence="high")
        self.assertEqual(v.choice, "doc::readme")
        self.assertEqual(v.quotes, ("held at 21.5 °C",))
        self.assertTrue(v.grounded)
        self.assertEqual(v.confidence, "high")

    def test_an_unlocatable_quote_caps_confidence_at_low(self):
        v = self._verdict(stated=True, quotes=["held at ... 21.5 °C"], confidence="high")
        self.assertEqual(v.choice, "doc::readme")
        self.assertFalse(v.grounded)
        self.assertEqual(v.confidence, "low")

    def test_every_quote_must_be_found(self):
        """One real sentence does not vouch for an invented one beside it."""
        v = self._verdict(stated=True, confidence="high",
                          quotes=["held at 21.5 °C", "kept in 30 psu seawater"])
        self.assertFalse(v.grounded)
        self.assertEqual(v.confidence, "low")

    def test_several_located_quotes_are_all_kept_once(self):
        v = self._verdict(stated=True, confidence="high",
                          quotes=["held at 21.5 °C", "12:12-h light–dark cycle", "held at 21.5 °C"])
        self.assertEqual(v.quotes, ("held at 21.5 °C", "12:12-h light–dark cycle"))
        self.assertTrue(v.grounded)

    def test_a_full_stop_added_where_a_sentence_was_cut_is_forgiven(self):
        v = self._verdict(stated=True, quotes=["Fish were held at 21.5 °C."], confidence="high")
        self.assertTrue(v.grounded)
        v = self._verdict(stated=True, quotes=["Fish were kept at 21.5 °C."], confidence="high")
        self.assertFalse(v.grounded)

    def test_a_single_quote_string_is_read_as_one_quote(self):
        v = self._verdict(stated=True, quote="held at 21.5 °C", confidence="high")
        self.assertEqual(v.quotes, ("held at 21.5 °C",))
        self.assertTrue(v.grounded)

    def test_no_quote_is_not_a_citation(self):
        v = self._verdict(stated=True, quotes=[], confidence="high")
        self.assertFalse(v.grounded)
        self.assertEqual(v.confidence, "low")

    def test_not_stated_is_a_first_class_abstention(self):
        v = self._verdict(stated=False, because="the passage gives no title")
        self.assertTrue(v.abstained)
        self.assertEqual(v.because, "the passage gives no title")

    def test_silence_about_a_field_is_not_an_answer(self):
        reply = json.dumps({"other": {"stated": True, "quote": "21.5"}})
        v = LLMPassageReader(_scripted(reply)).read(fields=[_field("f")], passage=PASSAGE)["f"]
        self.assertTrue(v.abstained)

    def test_garbled_output_abstains_rather_than_crashes(self):
        v = LLMPassageReader(_scripted("it says 21.5")).read(
            fields=[_field("f")], passage=PASSAGE
        )["f"]
        self.assertTrue(v.abstained)

    def test_a_raising_model_abstains_rather_than_crashes(self):
        def boom(_prompt):
            raise RuntimeError("connection reset")

        v = LLMPassageReader(boom).read(fields=[_field("f")], passage=PASSAGE)["f"]
        self.assertTrue(v.abstained)


class ReadManyTest(unittest.TestCase):
    OTHER = {"ref": "doc::methods", "text": "Fish were fed bloodworms."}

    def _requests(self):
        return [
            ([_field("one"), _field("two")], PASSAGE),
            ([_field("one")], self.OTHER),       # the same field, a second passage
        ]

    def test_one_call_per_passage_and_results_align_with_requests(self):
        def reply(prompt):
            if "bloodworms" in prompt:
                return json.dumps({"one": {"stated": True, "quote": "fed bloodworms"}})
            return json.dumps({"one": {"stated": False}, "two": {"stated": True,
                                                              "quote": "12:12-h"}})

        invoke = _scripted(reply)
        results = LLMPassageReader(invoke).read_many(requests=self._requests())
        self.assertEqual(len(invoke.prompts), 2)
        self.assertTrue(results[0]["one"].abstained)
        self.assertEqual(results[0]["two"].choice, "doc::readme")
        self.assertEqual(results[1]["one"].choice, "doc::methods")

    def test_batching_off_is_one_call_per_field_per_passage(self):
        invoke = _scripted("{}")
        LLMPassageReader(invoke, batch=False).read_many(requests=self._requests())
        self.assertEqual(len(invoke.prompts), 3)

    def test_concurrent_dispatch_keeps_results_aligned(self):
        results = LLMPassageReader(_scripted("{}"), batch=False, max_workers=4).read_many(
            requests=self._requests()
        )
        self.assertEqual([set(r) for r in results], [{"one", "two"}, {"one"}])

    def test_the_passage_precedes_the_fields_for_prefix_caching(self):
        invoke = _scripted("{}")
        LLMPassageReader(invoke).read(fields=[_field("one")], passage=PASSAGE)
        self.assertLess(invoke.prompts[0].index("PASSAGE"), invoke.prompts[0].index("FIELDS:"))

    def test_the_default_entrypoint_loops_read(self):
        class Single(PassageReader):
            def read(self, *, fields, passage):
                return {f.path: Verdict(choice=None) for f in fields}

        results = Single().read_many(requests=self._requests())
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
