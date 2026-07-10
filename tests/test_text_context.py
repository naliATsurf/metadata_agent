"""Tests for the TextContext implementation and its integration.

Covers the free-text context added in the multi-modality refactor: source
normalization, document access (read_text / chunking / search), typed
TextResourceInfo metadata, factory/classifier dispatch, and the tabular tool
gating that text contexts must trigger.
"""
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import (
    ContextType,
    TabularContext,
    TextContext,
    TextResourceInfo,
    classify_context_type,
    create_context,
)
from src.context.text_context import TextChunk, fixed_size_chunker
from src.tools import universal
from src.tools.base import clear_registry, register_context, tools_for
from src.tools.tabular import profiling, spatial, temporal


REPORT = (
    "Butterfly Monitoring Report\n"
    "\n"
    "Survey conducted in the Scottish Highlands in June 2019.\n"
    "\n"
    "We recorded 42 species across 12 transects.\n"
)
METHODS = "# Methods\n\nPollard walk methodology was used.\n"


class TextContextTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.report_path = os.path.join(self.dir, "report.txt")
        self.methods_path = os.path.join(self.dir, "methods.md")
        with open(self.report_path, "w") as f:
            f.write(REPORT)
        with open(self.methods_path, "w") as f:
            f.write(METHODS)

    def tearDown(self):
        self._tmp.cleanup()


class TestConstructionAndAccess(TextContextTestBase):
    def test_source_normalization(self):
        # str -> single resource named by stem
        single = TextContext(self.report_path)
        self.assertEqual(single.resources, ["report"])
        # list -> one resource per file
        listed = TextContext([self.report_path, self.methods_path])
        self.assertEqual(sorted(listed.resources), ["methods", "report"])
        # dict -> explicit resource names
        mapped = TextContext({"r": self.report_path, "m": self.methods_path})
        self.assertEqual(sorted(mapped.resources), ["m", "r"])

    def test_context_type_and_multi_resource(self):
        single = TextContext(self.report_path)
        self.assertEqual(single.context_type, ContextType.TEXT)
        self.assertFalse(single.is_multi_resource)
        multi = TextContext([self.report_path, self.methods_path])
        self.assertTrue(multi.is_multi_resource)

    def test_not_a_tabular_context(self):
        ctx = TextContext(self.report_path)
        self.assertNotIsInstance(ctx, TabularContext)
        self.assertFalse(hasattr(ctx, "read_resource"))

    def test_read_text_full_and_limited(self):
        ctx = TextContext(self.report_path)
        self.assertEqual(ctx.read_text("report"), REPORT)
        self.assertEqual(ctx.read_text("report", limit=10), REPORT[:10])

    def test_unknown_resource_raises(self):
        ctx = TextContext(self.report_path)
        with self.assertRaises(ValueError):
            ctx.read_text("nope")


class TestChunking(TextContextTestBase):
    def test_paragraph_chunks(self):
        ctx = TextContext(self.report_path)
        chunks = ctx.get_chunks("report")
        self.assertEqual(len(chunks), 3)
        self.assertIsInstance(chunks[0], TextChunk)
        self.assertEqual(chunks[0].text, "Butterfly Monitoring Report")
        self.assertTrue(chunks[1].text.startswith("Survey conducted"))
        # indices are sequential, offsets strictly increasing, text stripped
        self.assertEqual([c.index for c in chunks], [0, 1, 2])
        self.assertTrue(chunks[0].start_offset < chunks[1].start_offset < chunks[2].start_offset)
        self.assertTrue(all(c.text == c.text.strip() for c in chunks))
        self.assertEqual(chunks[0].char_count, len("Butterfly Monitoring Report"))

    def test_iter_chunks_matches_get_chunks(self):
        ctx = TextContext(self.report_path)
        self.assertEqual(
            [c.text for c in ctx.iter_chunks("report")],
            [c.text for c in ctx.get_chunks("report")],
        )

    def test_custom_chunker_overrides_default(self):
        # A per-line chunker: offset at every newline boundary.
        def line_chunker(text):
            offsets = [0]
            for i, ch in enumerate(text):
                if ch == "\n" and i + 1 < len(text):
                    offsets.append(i + 1)
            return offsets

        ctx = TextContext(self.report_path, chunker=line_chunker)
        # 3 non-blank lines survive stripping of blank ones
        texts = [c.text for c in ctx.get_chunks("report")]
        self.assertEqual(len(texts), 3)
        self.assertIn("Butterfly Monitoring Report", texts)

    def test_fixed_size_chunker(self):
        chunker = fixed_size_chunker(20)
        ctx = TextContext(self.report_path, chunker=chunker)
        chunks = ctx.get_chunks("report")
        self.assertGreater(len(chunks), 3)


class TestSearch(TextContextTestBase):
    def test_plain_search(self):
        ctx = TextContext([self.report_path, self.methods_path])
        hits = ctx.search("transects")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["resource"], "report")
        self.assertIn("transects", hits[0]["context"])

    def test_regex_search(self):
        ctx = TextContext(self.report_path)
        hits = ctx.search(r"\d+ species", regex=True)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["match"], "42 species")

    def test_search_resource_filter_and_case_insensitive(self):
        ctx = TextContext([self.report_path, self.methods_path])
        self.assertEqual(ctx.search("pollard", resource="methods")[0]["resource"], "methods")
        self.assertEqual(ctx.search("BUTTERFLY"), ctx.search("butterfly"))

    def test_search_max_results(self):
        ctx = TextContext(self.report_path)
        hits = ctx.search("e", max_results=3)  # common letter
        self.assertEqual(len(hits), 3)


class TestResourceInfo(TextContextTestBase):
    def test_typed_text_resource_info(self):
        ctx = TextContext(self.report_path)
        info = ctx.get_resource_info("report")
        self.assertIsInstance(info, TextResourceInfo)
        self.assertEqual(info.item_count, 3)  # chunk count
        self.assertEqual(info.char_count, len(REPORT))
        self.assertEqual(info.word_count, len(REPORT.split()))
        self.assertEqual(info.encoding, "utf-8")
        self.assertIn("Butterfly", info.description)

    def test_to_dict_is_text_flavored(self):
        info = TextContext(self.report_path).get_resource_info("report")
        d = info.to_dict()
        self.assertEqual(d["kind"], "text")
        self.assertIn("word_count", d)
        # tabular-only keys must not leak into a text resource's serialization
        self.assertNotIn("field_count", d)
        self.assertNotIn("fields", d)

    def test_summary_mentions_chunks(self):
        info = TextContext(self.report_path).get_resource_info("report")
        self.assertIn("chunks", info.summary())

    def test_schema_and_validate(self):
        ctx = TextContext([self.report_path, self.methods_path])
        schema = ctx.get_schema()
        self.assertEqual(schema["context_type"], "text")
        self.assertTrue(schema["is_multi_resource"])
        self.assertTrue(ctx.validate())


class TestFactoryAndClassifier(TextContextTestBase):
    def test_factory_dispatches_text(self):
        self.assertIsInstance(create_context(self.report_path), TextContext)
        self.assertIsInstance(create_context(self.methods_path), TextContext)
        self.assertIsInstance(
            create_context([self.report_path, self.methods_path]), TextContext
        )

    def test_factory_directory_of_text(self):
        ctx = create_context(self.dir)
        self.assertIsInstance(ctx, TextContext)
        self.assertEqual(sorted(ctx.resources), ["methods", "report"])

    def test_classifier(self):
        self.assertEqual(classify_context_type([self.report_path]), ContextType.TEXT)
        self.assertEqual(
            classify_context_type([self.report_path, self.methods_path]),
            ContextType.TEXT,
        )
        self.assertEqual(classify_context_type([self.dir]), ContextType.TEXT)


class TestToolGating(TextContextTestBase):
    def setUp(self):
        super().setUp()
        self.ctx = TextContext(self.report_path)
        self.key = register_context(f"ctx_test_text_{uuid.uuid4().hex[:8]}", self.ctx)

    def tearDown(self):
        clear_registry()
        super().tearDown()

    def test_generic_tools_work_on_text(self):
        overview = universal.get_context_overview.invoke({"context_key": self.key})
        self.assertEqual(overview["context_type"], "text")
        self.assertEqual(universal.get_item_count.invoke({"context_key": self.key}), 3)

    def test_sample_items_returns_chunks(self):
        sample = universal.get_sample_items.invoke({"context_key": self.key, "n": 2})
        self.assertIn("Butterfly Monitoring Report", sample)

    def test_tabular_tools_refuse_text(self):
        for tool in [
            profiling.get_field_statistics,
            profiling.get_field_names,
            temporal.detect_temporal_columns,
            spatial.detect_spatial_columns,
        ]:
            with self.assertRaises(TypeError) as cm:
                tool.invoke({"context_key": self.key})
            self.assertIn("TabularContext", str(cm.exception))

    def test_text_context_is_offered_only_universal_tools(self):
        """Capability gating replaces the old context-type table."""
        offered = {t.name for t in tools_for(self.ctx)}
        self.assertIn("get_context_overview", offered)
        self.assertIn("get_sample_items", offered)
        self.assertNotIn("get_field_names", offered)
        self.assertNotIn("detect_spatial_columns", offered)


class TestRealSampleDocument(unittest.TestCase):
    """Exercises a realistic document if the sample fixture is present."""

    def test_real_abstract(self):
        path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "data",
                "sample",
                "sharetrait",
                "TRADAT001.txt",
            )
        )
        if not os.path.isfile(path):
            self.skipTest(f"sample text fixture missing: {path}")

        ctx = create_context(path, name="sharetrait_abstract")
        self.assertIsInstance(ctx, TextContext)
        info = ctx.get_resource_info(ctx.resources[0])
        self.assertIsInstance(info, TextResourceInfo)
        self.assertGreater(info.word_count, 100)
        self.assertGreater(info.item_count, 0)
        hits = ctx.search("diapause")
        self.assertTrue(hits)


if __name__ == "__main__":
    unittest.main()
