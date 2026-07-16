"""Tests for the run-scoped evidence ledger captured at the tool boundary."""

import os
import sys
import tempfile
import unittest
import uuid

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context.context_factory import create_context
from src.provenance import (
    attribute_metadata,
    clear_evidence,
    get_evidence,
    serialize_evidence,
)
from src.tools.base import clear_registry, register_context
from src.tools import universal
from src.tools.tabular import profiling


class EvidenceLedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "measurements.csv")
        pd.DataFrame({"id": [1, 2, 3], "value": [10, 20, 30]}).to_csv(
            self.csv, index=False
        )
        ctx = create_context(self.csv, name="measurements")
        self.key = register_context(f"k_{uuid.uuid4().hex[:8]}", ctx)
        clear_evidence()

    def tearDown(self):
        clear_registry()

    def _invoke(self, tool, **kwargs):
        return tool.invoke({"context_key": self.key, **kwargs})

    def test_tool_call_is_captured(self):
        self._invoke(universal.list_resources)
        entries = get_evidence(self.key)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].tool, "list_resources")
        self.assertIsNotNone(entries[0].result)

    def test_resource_and_args_are_separated(self):
        """The resource is captured distinctly from a tool's other arguments.

        Defaults are captured too (``limit`` here): replay-based verification
        needs the full argument set the tool actually ran with.
        """
        self._invoke(profiling.get_unique_values, resource="measurements", field="value")
        entry = get_evidence(self.key)[-1]

        self.assertEqual(entry.tool, "get_unique_values")
        self.assertEqual(entry.resource, "measurements")
        self.assertEqual(entry.args.get("field"), "value")
        self.assertNotIn("resource", entry.args)
        self.assertNotIn("context_key", entry.args)

    def test_evidence_ids_are_unique_and_ordered(self):
        self._invoke(universal.list_resources)
        self._invoke(profiling.get_field_names, resource="measurements")
        ids = [e.id for e in get_evidence(self.key)]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))

    def test_describe_renders_a_citation(self):
        self._invoke(profiling.get_field_names, resource="measurements")
        entry = get_evidence(self.key)[-1]
        self.assertEqual(entry.describe(), "get_field_names(measurements)")

    def test_evidence_is_scoped_per_context_key(self):
        """A second run's context does not see the first run's facts."""
        other_ctx = create_context(self.csv, name="other")
        other_key = register_context(f"k_{uuid.uuid4().hex[:8]}", other_ctx)

        self._invoke(universal.list_resources)  # recorded under self.key

        self.assertEqual(len(get_evidence(self.key)), 1)
        self.assertEqual(get_evidence(other_key), [])

    def test_failed_tool_records_nothing(self):
        """A tool that raises produced no fact, so nothing is captured."""
        with self.assertRaises(Exception):
            self._invoke(profiling.get_unique_values, resource="nonexistent", field="value")
        self.assertEqual(get_evidence(self.key), [])

    def test_serialize_evidence_is_plain_dicts(self):
        self._invoke(universal.get_item_count, resource="measurements")
        rows = serialize_evidence(self.key)

        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {
            "id", "context_key", "tool", "resource", "args", "result", "citation",
        })

    def test_clear_registry_also_clears_evidence(self):
        self._invoke(universal.list_resources)
        self.assertTrue(get_evidence(self.key))

        clear_registry()
        self.assertEqual(get_evidence(self.key), [])


class TabularAttributionTest(unittest.TestCase):
    """Deterministic attribution of metadata values to captured tool evidence."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "obs.csv")
        pd.DataFrame(
            {"sample_id": [1, 2, 3], "weight": [10, 20, 30]}
        ).to_csv(self.csv, index=False)
        ctx = create_context(self.csv, name="obs")
        self.key = register_context(f"k_{uuid.uuid4().hex[:8]}", ctx)
        clear_evidence()
        # Populate the ledger the way the survey phase would.
        universal.list_resources.invoke({"context_key": self.key})
        universal.get_item_count.invoke({"context_key": self.key, "resource": "obs"})
        profiling.get_field_names.invoke({"context_key": self.key, "resource": "obs"})

    def tearDown(self):
        clear_registry()

    def _attribute(self, metadata):
        return attribute_metadata(metadata, get_evidence(self.key))

    def test_value_equal_to_a_whole_result_is_verbatim(self):
        prov = self._attribute({"columns": ["sample_id", "weight"]})["columns"]
        self.assertEqual(prov["status"], "filled")
        self.assertEqual(prov["transform"], "verbatim")
        self.assertIn("get_field_names", prov["source_ref"])

    def test_scalar_value_traces_to_its_producing_tool(self):
        prov = self._attribute({"row_count": 3})["row_count"]
        self.assertEqual(prov["status"], "filled")
        self.assertIn("get_item_count", prov["source_ref"])
        self.assertEqual(prov["evidence_id"][:3], "ev_")

    def test_unsupported_value_is_unverifiable(self):
        """The confabulation signal: a value nothing in the run supports."""
        prov = self._attribute({"title": "Arctic tern migration survey"})["title"]
        self.assertEqual(prov["status"], "unverifiable")
        self.assertIsNone(prov["source_ref"])

    def test_absent_value_is_not_present(self):
        prov = self._attribute({"subject": None})["subject"]
        self.assertEqual(prov["status"], "not_present")

    def test_zero_is_a_value_not_an_absence(self):
        # get_missing_values reports 0 for every column here; 0 must be traced,
        # not treated as missing.
        profiling.get_missing_values.invoke({"context_key": self.key, "resource": "obs"})
        prov = self._attribute({"missing_in_weight": 0}, )["missing_in_weight"]
        self.assertEqual(prov["status"], "filled")

    def test_sidecar_is_parallel_to_the_value_record(self):
        metadata = {"columns": ["sample_id", "weight"], "title": "made up"}
        prov = self._attribute(metadata)
        self.assertEqual(set(prov), set(metadata))          # same keys
        self.assertEqual(prov["columns"]["status"], "filled")
        self.assertEqual(prov["title"]["status"], "unverifiable")


if __name__ == "__main__":
    unittest.main()
