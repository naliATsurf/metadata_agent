"""Tests for counting model calls (see src/llm_calls.py). A fake model; no network."""

import os
import sys
import threading
import unittest

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.prompts import ChatPromptTemplate

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.llm_calls import count_llm_calls
from src.router.column_matcher import LLMColumnMatcher
from src.router.schema import FieldSpec


def _model(reply: str = "ok") -> FakeListChatModel:
    return FakeListChatModel(responses=[reply])


class CountLLMCallsTest(unittest.TestCase):
    def test_counts_each_call_in_the_block(self):
        model = _model()
        with count_llm_calls() as counter:
            model.invoke("a")
            model.invoke("b")
        self.assertEqual(counter.calls, 2)

    def test_a_call_inside_a_chain_counts_once(self):
        chain = ChatPromptTemplate.from_template("{x}") | _model()
        with count_llm_calls() as counter:
            chain.invoke({"x": "a"})
        self.assertEqual(counter.calls, 1)

    def test_calls_outside_the_block_are_not_counted(self):
        model = _model()
        model.invoke("before")
        with count_llm_calls() as counter:
            pass
        model.invoke("after")
        self.assertEqual(counter.calls, 0)

    def test_concurrent_blocks_count_separately(self):
        counts = {}

        def run(name: str, calls: int) -> None:
            model = _model()
            with count_llm_calls() as counter:
                for _ in range(calls):
                    model.invoke("x")
            counts[name] = counter.calls

        threads = [threading.Thread(target=run, args=(n, c)) for n, c in [("a", 3), ("b", 5)]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(counts, {"a": 3, "b": 5})

    def test_the_judge_thread_pool_is_counted(self):
        model = _model('{"f0": {"choice": null}}')
        matcher = LLMColumnMatcher(
            lambda prompt: model.invoke(prompt).content, batch=False, max_workers=4
        )
        cards = [{"ref": "t::c", "kind": "column", "meaning": "a column"}]
        fields = [
            FieldSpec(path=f"f{i}", description=f"field {i}", type="str", required=False)
            for i in range(3)
        ]
        with count_llm_calls() as counter:
            matcher.match_many(requests=[(fields, cards)])
        self.assertEqual(counter.calls, 3)

if __name__ == "__main__":
    unittest.main()
