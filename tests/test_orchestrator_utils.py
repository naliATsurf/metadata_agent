import unittest
import sys
import os
import tempfile

import pandas as pd

# Add the src directory to the Python path to allow for absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.context.context_factory import create_context
from src.orchestrator.utils import validate_plan_columns, validate_plan_dataflow
from src.tools.base import clear_registry

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

    def test_column_reference_on_text_context_is_rejected(self):
        plan = [{"task": "get_spatial_extent", "inputs": {"lat_column": "lat"}}]
        ok, msg = validate_plan_columns(plan, self.text_ctx)
        self.assertFalse(ok)

    def test_plan_without_column_params_is_valid(self):
        plan = [{"task": "get_field_statistics", "inputs": {}, "outputs": ["stats"]}]
        ok, _ = validate_plan_columns(plan, self.ctx)
        self.assertTrue(ok)


if __name__ == '__main__':
    unittest.main()
