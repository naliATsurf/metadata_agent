"""Tests for capability-based tool gating and dispatch-flag derivation."""

import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context.base_context import ExecutionContext
from src.context.context_factory import create_context
from src.orchestrator.utils import validate_plan_tool_compatibility
from src.tools.base import (
    all_tools,
    clear_registry,
    is_auto_fireable,
    is_resource_scoped,
    register_context,
    registered_toolsets,
    requires_of,
    resolve_toolsets,
    tools_for,
)
from src.tools.tabular import profiling, spatial


class ToolRegistryTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

        self.csv_a = os.path.join(self.dir, "a.csv")
        pd.DataFrame(
            {"id": [1, 2], "lat": [54.1, 54.2], "lon": [-7.8, -7.9]}
        ).to_csv(self.csv_a, index=False)

        self.csv_b = os.path.join(self.dir, "b.csv")
        pd.DataFrame({"id": [1, 2], "value": [3, 4]}).to_csv(self.csv_b, index=False)

        self.doc = os.path.join(self.dir, "doc.md")
        with open(self.doc, "w") as f:
            f.write("# Title\n\nFirst para.\n\nSecond para.\n")

        self.db = os.path.join(self.dir, "x.sqlite")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        con.execute("INSERT INTO t VALUES (1, 'x')")
        con.commit()
        con.close()

    def tearDown(self):
        clear_registry()


class TestCapabilityGating(ToolRegistryTestBase):
    def test_tabular_contexts_get_tabular_tools(self):
        """CSV and SQLite are different formats but the same capability."""
        for source in (self.csv_a, self.db):
            ctx = create_context(source, name="t")
            offered = {t.name for t in tools_for(ctx)}
            self.assertIn("get_field_names", offered)
            self.assertIn("detect_spatial_columns", offered)
            self.assertIn("get_context_overview", offered)

    def test_text_context_gets_only_universal_tools(self):
        ctx = create_context(self.doc, name="t")
        offered = {t.name for t in tools_for(ctx)}
        self.assertIn("get_context_overview", offered)
        self.assertIn("get_sample_items", offered)
        self.assertNotIn("get_field_names", offered)

    def test_every_tool_declares_a_context_capability(self):
        for t in all_tools():
            self.assertTrue(issubclass(requires_of(t), ExecutionContext))

    def test_relationships_tool_gated_on_cardinality(self):
        """available_when covers what capability cannot express."""
        single = create_context(self.csv_a, name="single")
        multi = create_context([self.csv_a, self.csv_b], name="multi")

        self.assertNotIn("get_relationships", {t.name for t in tools_for(single)})
        self.assertIn("get_relationships", {t.name for t in tools_for(multi)})

    def test_tabular_tool_raises_on_text_context(self):
        ctx = create_context(self.doc, name="t")
        key = register_context(f"k_{uuid.uuid4().hex[:8]}", ctx)

        with self.assertRaises(TypeError) as cm:
            profiling.get_field_names.invoke({"context_key": key})
        self.assertIn("TabularContext", str(cm.exception))


class TestDispatchFlags(ToolRegistryTestBase):
    def test_auto_fireable_iff_no_model_supplied_args(self):
        """A tool needing a column name cannot be fired blindly."""
        self.assertTrue(is_auto_fireable(profiling.get_field_names))
        self.assertTrue(is_auto_fireable(spatial.detect_spatial_columns))

        self.assertFalse(is_auto_fireable(profiling.get_unique_values))
        self.assertFalse(is_auto_fireable(spatial.get_spatial_extent))
        self.assertFalse(is_auto_fireable(spatial.analyze_spatial_column))

    def test_resource_scoped_derived_from_signature(self):
        self.assertTrue(is_resource_scoped(profiling.get_field_names))
        self.assertTrue(is_resource_scoped(spatial.detect_spatial_columns))
        self.assertFalse(is_resource_scoped(_by_name("get_context_overview")))
        self.assertFalse(is_resource_scoped(_by_name("list_resources")))

    def test_detection_tools_are_auto_fireable_analysis_tools_are_not(self):
        """The split that the old name-keyword dispatcher got wrong."""
        detect = [t for t in all_tools() if t.name.startswith("detect_")]
        analyze = [t for t in all_tools() if t.name.startswith("analyze_")]

        self.assertTrue(detect and analyze)
        self.assertTrue(all(is_auto_fireable(t) for t in detect))
        self.assertTrue(all(not is_auto_fireable(t) for t in analyze))

    def test_context_key_is_hidden_from_tool_body_but_present_in_schema(self):
        self.assertIn("context_key", profiling.get_field_names.args)
        self.assertNotIn("ctx", profiling.get_field_names.args)


class TestToolsetResolution(ToolRegistryTestBase):
    def test_glob_patterns_expand_across_modalities(self):
        ctx = create_context(self.csv_a, name="t")
        names = {t.name for t in resolve_toolsets(["*.profiling"], ctx)}
        self.assertIn("get_field_names", names)
        self.assertNotIn("get_context_overview", names)

    def test_resolution_is_gated_by_context(self):
        text_ctx = create_context(self.doc, name="t")
        self.assertEqual(resolve_toolsets(["tabular.profiling"], text_ctx), [])

    def test_registered_toolsets_are_dotted_names(self):
        self.assertIn("universal", registered_toolsets())
        self.assertIn("tabular.spatial", registered_toolsets())


class TestPlanToolCompatibility(ToolRegistryTestBase):
    def test_partly_tabular_player_accepted_on_text_context(self):
        """data_analyst wants tabular.profiling, but 'universal' still resolves."""
        ctx = create_context(self.doc, name="t")
        plan = [{"task": "profile", "player": "data_analyst"}]

        ok, _ = validate_plan_tool_compatibility(plan, ctx)
        self.assertTrue(ok)

    def test_unknown_player_rejected(self):
        ctx = create_context(self.csv_a, name="t")
        plan = [{"task": "x", "player": "nonexistent"}]
        ok, msg = validate_plan_tool_compatibility(plan, ctx)
        self.assertFalse(ok)
        self.assertIn("unknown player", msg)

    def test_metadata_generator_now_valid_on_text_and_sqlite(self):
        """Regression: this combination used to abort every run."""
        plan = [{"task": "generate", "player": "metadata_generator"}]
        for source in (self.doc, self.db):
            ctx = create_context(source, name="t")
            ok, msg = validate_plan_tool_compatibility(plan, ctx)
            self.assertTrue(ok, f"{ctx.context_type.value} rejected: {msg}")

    def test_toolless_player_always_valid(self):
        ctx = create_context(self.doc, name="t")
        plan = [{"task": "review", "player": "critic"}]
        ok, _ = validate_plan_tool_compatibility(plan, ctx)
        self.assertTrue(ok)


def _by_name(name: str):
    return next(t for t in all_tools() if t.name == name)


if __name__ == "__main__":
    unittest.main()
