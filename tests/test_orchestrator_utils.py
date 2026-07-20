import unittest
import sys
import os
import tempfile

import pandas as pd

# Add the src directory to the Python path to allow for absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.context.context_factory import create_context
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.utils import validate_plan_columns, validate_plan_dataflow
from src.tools.base import _CONTEXT_REGISTRY, clear_registry

class TestPlanValidation(unittest.TestCase):

    def test_valid_sequential_plan(self):
        """Tests a plan with a correct sequence of dependencies."""
        plan = [
            {
                "task": "list_tables",
                "inputs": {},
                "outputs": ["table_list"],
            },
            {
                "task": "get_schemas",
                "inputs": {"tables": "table_list"},
                "outputs": ["table_schemas"],
            },
        ]
        is_valid, message = validate_plan_dataflow(plan)
        self.assertTrue(is_valid)
        self.assertEqual(message, "Plan dataflow is valid.")

    def test_invalid_plan_missing_input(self):
        """Tests a plan where an input artifact is never produced."""
        plan = [
            {
                "task": "get_schemas",
                "inputs": {"tables": "table_list"}, # "table_list" is never created
                "outputs": ["table_schemas"],
            },
        ]
        is_valid, message = validate_plan_dataflow(plan)
        self.assertFalse(is_valid)
        self.assertIn("requires artifact 'table_list'", message)
        self.assertIn("not produced by any preceding step", message)

    def test_invalid_plan_incorrect_order(self):
        """Tests a plan where steps are in the wrong logical order."""
        plan = [
            {
                "task": "get_schemas",
                "inputs": {"tables": "table_list"},
                "outputs": ["table_schemas"],
            },
            {
                "task": "list_tables",
                "inputs": {},
                "outputs": ["table_list"],
            },
        ]
        is_valid, message = validate_plan_dataflow(plan)
        self.assertFalse(is_valid)
        self.assertIn("Step 1 ('get_schemas')", message)
        self.assertIn("requires artifact 'table_list'", message)

    def test_valid_plan_no_dependencies(self):
        """Tests a valid plan where no steps have input dependencies."""
        plan = [
            {
                "task": "get_row_count",
                "inputs": {},
                "outputs": ["row_count"],
            },
            {
                "task": "get_column_names",
                "inputs": {},
                "outputs": ["column_names"],
            },
        ]
        is_valid, message = validate_plan_dataflow(plan)
        self.assertTrue(is_valid)
        self.assertEqual(message, "Plan dataflow is valid.")

    def test_empty_plan(self):
        """Tests that an empty plan is considered valid."""
        plan = []
        is_valid, message = validate_plan_dataflow(plan)
        self.assertTrue(is_valid)
        self.assertEqual(message, "Plan dataflow is valid.")

    def test_valid_plan_multiple_inputs(self):
        """Tests a valid plan with a step that requires multiple inputs."""
        plan = [
            {"task": "get_tables", "inputs": {}, "outputs": ["table_list"]},
            {"task": "get_columns", "inputs": {}, "outputs": ["column_list"]},
            {
                "task": "join_info",
                "inputs": {"tables": "table_list", "columns": "column_list"},
                "outputs": ["joined_info"],
            },
        ]
        is_valid, message = validate_plan_dataflow(plan)
        self.assertTrue(is_valid)
        self.assertEqual(message, "Plan dataflow is valid.")


class TestColumnValidation(unittest.TestCase):
    """A plan may not reference a column the data does not contain."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "obs.csv")
        pd.DataFrame({"sample_id": [1, 2], "Depth": [3.0, 4.0]}).to_csv(
            self.csv, index=False
        )
        self.ctx = create_context(self.csv, name="obs")

        self.doc = os.path.join(self.dir, "notes.md")
        with open(self.doc, "w") as f:
            f.write("# Notes\n\nSome prose.\n")
        self.text_ctx = create_context(self.doc, name="notes")

    def tearDown(self):
        clear_registry()

    def test_invented_column_is_rejected(self):
        plan = [
            {"task": "get_spatial_extent",
             "inputs": {"lat_column": "latitude", "lon_column": "longitude"}},
        ]
        ok, msg = validate_plan_columns(plan, self.ctx)
        self.assertFalse(ok)
        self.assertIn("latitude", msg)

    def test_real_column_passes_case_insensitively(self):
        # 'depth' matches the real 'Depth' column regardless of case.
        plan = [{"task": "analyze", "inputs": {"target_column": "depth"}}]
        ok, _ = validate_plan_columns(plan, self.ctx)
        self.assertTrue(ok)

    def test_artifact_wiring_is_not_treated_as_a_column(self):
        """Keys like field_stats / columns wire artifacts, not columns."""
        plan = [
            {"task": "generate",
             "inputs": {"field_stats": "stats_artifact", "columns": "column_list"}},
        ]
        ok, _ = validate_plan_columns(plan, self.ctx)
        self.assertTrue(ok)

    def test_produced_artifact_under_a_column_key_is_allowed(self):
        """Regression: a detection output wired into a later step is not a column.

        A `*_columns` key can legitimately carry the artifact a prior step
        produced; that is dataflow wiring, not an invented column.
        """
        plan = [
            {"task": "detect_spatial_columns", "inputs": {},
             "outputs": ["spatial_columns"]},
            {"task": "get_spatial_extent",
             "inputs": {"spatial_columns": "spatial_columns"}, "outputs": ["extent"]},
        ]
        ok, msg = validate_plan_columns(plan, self.ctx)
        self.assertTrue(ok, msg)

    def test_value_that_is_neither_column_nor_artifact_still_rejected(self):
        """The real failure mode survives: a value naming nothing at all."""
        plan = [
            {"task": "get_spatial_extent",
             "inputs": {"lat_column": "latitude"}, "outputs": ["extent"]},
        ]
        ok, msg = validate_plan_columns(plan, self.ctx)
        self.assertFalse(ok)
        self.assertIn("latitude", msg)

    def test_column_reference_on_text_context_is_rejected(self):
        plan = [{"task": "get_spatial_extent", "inputs": {"lat_column": "lat"}}]
        ok, msg = validate_plan_columns(plan, self.text_ctx)
        self.assertFalse(ok)

    def test_plan_without_column_params_is_valid(self):
        plan = [{"task": "get_field_statistics", "inputs": {}, "outputs": ["stats"]}]
        ok, _ = validate_plan_columns(plan, self.ctx)
        self.assertTrue(ok)


class TestContextInspection(unittest.TestCase):
    """inspect-then-plan: the planner is handed detected content, not just names."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Skip __init__ (needs an LLM provider); _inspect_context needs no state.
        self.orch = Orchestrator.__new__(Orchestrator)

    def tearDown(self):
        clear_registry()

    def _csv(self, name, frame):
        path = os.path.join(self.dir, name)
        pd.DataFrame(frame).to_csv(path, index=False)
        return create_context(path, name=name.replace(".csv", ""))

    def test_profile_reports_the_real_columns(self):
        """The profile is a generic tool sweep — it surfaces the actual schema."""
        ctx = self._csv("plain.csv", {"species": ["ab", "cd"], "count": [1000, 2000]})
        profile = self.orch._inspect_context(ctx)
        self.assertIn("get_field_names", profile)   # the sweep ran
        self.assertIn("species", profile)           # real columns surfaced
        self.assertIn("count", profile)

    def test_profile_surfaces_detected_coordinates_generically(self):
        """No spatial-specific code: detection appears because the tool ran."""
        ctx = self._csv(
            "sight.csv",
            {"lat": [54.1, 54.2], "lon": [-7.8, -7.9], "observed": ["2019-01", "2019-06"]},
        )
        profile = self.orch._inspect_context(ctx)
        self.assertIn("detect_spatial_columns", profile)
        self.assertIn("lat", profile)

    def test_inspection_leaves_no_registered_context_behind(self):
        ctx = self._csv("plain.csv", {"a": [1, 2]})
        self.orch._inspect_context(ctx)
        self.assertFalse([k for k in _CONTEXT_REGISTRY if k.startswith("inspect_")])


if __name__ == '__main__':
    unittest.main()
