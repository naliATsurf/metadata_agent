"""Tests for the Searchable capability and the search_context tool (layer 2)."""

import os
import sys
import tempfile
import unittest
import uuid

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import EvidenceRef, Searchable, create_context
from src.provenance import clear_evidence, get_evidence
from src.tools import search as search_module  # noqa: F401 (registers search_context)
from src.tools.base import (
    clear_registry,
    is_auto_fireable,
    register_context,
    tools_for,
)


class TabularSearchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        clear_registry()

    def _ctx(self, frame, name):
        path = os.path.join(self.dir, f"{name}.csv")
        frame.to_csv(path, index=False)
        return create_context(path, name=name)

    def test_descriptive_column_is_matched(self):
        ctx = self._ctx(
            pd.DataFrame({"id": [1, 2], "latitude": [10.0, 11.0], "longitude": [0.1, 0.2]}),
            "descriptive",
        )
        refs = ctx.search("latitude")
        self.assertTrue(refs)
        self.assertEqual(refs[0].locator, "latitude")
        self.assertEqual(refs[0].kind, "computed_column")

    def test_opaque_column_is_not_matched(self):
        """The semantic gap: an opaque name scores 0 against a semantic query."""
        ctx = self._ctx(
            pd.DataFrame({"la": [10.0, 11.0], "lo": [0.1, 0.2]}), "opaque"
        )
        self.assertEqual(ctx.search("latitude"), [])

    def test_results_are_ranked_and_capped(self):
        ctx = self._ctx(
            pd.DataFrame({"temperature": [1], "temperature_flag": [0], "site": ["x"]}),
            "ranked",
        )
        refs = ctx.search("temperature", k=1)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].locator, "temperature")

    def test_context_is_searchable(self):
        ctx = self._ctx(pd.DataFrame({"a": [1]}), "cap")
        self.assertIsInstance(ctx, Searchable)


class TextSearchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Plain text so paragraph chunking gives distinct chunks to rank.
        self.path = os.path.join(self.dir, "doc.txt")
        with open(self.path, "w") as f:
            f.write(
                "The survey covered upland habitats.\n\n"
                "The dataset is released under a permissive licence for reuse.\n\n"
                "Contact the data office for enquiries.\n"
            )
        self.ctx = create_context(self.path, name="doc")

    def tearDown(self):
        clear_registry()

    def test_returns_ranked_quoted_spans(self):
        refs = self.ctx.search("licence reuse")
        self.assertTrue(refs)
        top = refs[0]
        self.assertEqual(top.kind, "quoted_span")
        self.assertIsInstance(top.locator, tuple)
        self.assertIn("licence", top.snippet)

    def test_absent_term_returns_nothing(self):
        self.assertEqual(self.ctx.search("photosynthesis"), [])

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.ctx.search(""), [])

    def test_stopword_only_overlap_does_not_match(self):
        """BM25 gate: a chunk sharing only a stop word ('for') must not surface."""
        # "licence" and "reuse" are content terms; only they should drive a match,
        # never the shared "for" / "the" — the noise the old scorer surfaced.
        refs = self.ctx.search("for the")
        self.assertEqual(refs, [])

    def test_discriminative_term_ranks_its_chunk_first(self):
        refs = self.ctx.search("upland habitats")
        self.assertTrue(refs)
        self.assertIn("upland", refs[0].snippet)


class SearchToolTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        path = os.path.join(self.dir, "obs.csv")
        pd.DataFrame({"latitude": [10.0], "longitude": [0.1]}).to_csv(path, index=False)
        self.ctx = create_context(path, name="obs")
        self.key = register_context(f"k_{uuid.uuid4().hex[:8]}", self.ctx)
        clear_evidence()

    def tearDown(self):
        clear_registry()

    def _tool(self):
        return next(t for t in tools_for(self.ctx) if t.name == "search_context")

    def test_tool_is_offered_to_a_searchable_context(self):
        self.assertIn("search_context", [t.name for t in tools_for(self.ctx)])

    def test_tool_is_not_auto_fireable(self):
        """It needs a model/router-supplied query, so the survey never fires it."""
        self.assertFalse(is_auto_fireable(self._tool()))

    def test_tool_returns_serializable_refs(self):
        out = self._tool().invoke({"context_key": self.key, "query": "latitude"})
        self.assertTrue(out)
        self.assertEqual(out[0]["kind"], "computed_column")
        self.assertEqual(out[0]["locator"], "latitude")

    def test_tool_call_is_captured_as_evidence(self):
        self._tool().invoke({"context_key": self.key, "query": "latitude"})
        entries = get_evidence(self.key)
        self.assertTrue(entries)
        self.assertEqual(entries[-1].tool, "search_context")
        self.assertTrue(entries[-1].used_by)  # a use was recorded


if __name__ == "__main__":
    unittest.main()
