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
    Caller,
    attribute_metadata,
    attributed_to,
    clear_evidence,
    get_evidence,
    serialize_evidence,
)
from src.tools.base import (
    clear_registry,
    get_context,
    is_auto_fireable,
    register_context,
    tools_for,
    unregister_context,
)
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
            "used_by",
        })

    def test_clear_registry_also_clears_evidence(self):
        self._invoke(universal.list_resources)
        self.assertTrue(get_evidence(self.key))

        clear_registry()
        self.assertEqual(get_evidence(self.key), [])

    def test_unregister_removes_only_its_own_context_and_evidence(self):
        """Per-run teardown drops one run's context and evidence, not others'."""
        other_ctx = create_context(self.csv, name="other")
        other_key = register_context(f"k_{uuid.uuid4().hex[:8]}", other_ctx)

        self._invoke(universal.list_resources)                       # under self.key
        other_ctx_tool = universal.list_resources
        other_ctx_tool.invoke({"context_key": other_key})            # under other_key

        unregister_context(self.key)

        # self.key is gone — context unresolvable, evidence dropped
        self.assertEqual(get_evidence(self.key), [])
        with self.assertRaises(KeyError):
            get_context(self.key)
        # the other run is untouched
        self.assertTrue(get_evidence(other_key))
        self.assertIsNotNone(get_context(other_key))


class ToolResultCacheTest(unittest.TestCase):
    """Repeated identical tool calls within a run are served from cache."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "obs.csv")
        pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}).to_csv(self.csv, index=False)
        self.ctx = create_context(self.csv, name="obs")
        self.key = register_context(f"k_{uuid.uuid4().hex[:8]}", self.ctx)
        clear_evidence()

    def tearDown(self):
        clear_registry()

    def test_identical_call_is_served_from_cache(self):
        r1 = universal.list_resources.invoke({"context_key": self.key})
        r2 = universal.list_resources.invoke({"context_key": self.key})
        self.assertEqual(r1, r2)
        # Recorded once → the second call was a cache hit (no re-run, no duplicate).
        self.assertEqual(len(get_evidence(self.key)), 1)

    def test_different_args_are_cached_separately(self):
        universal.get_sample_items.invoke({"context_key": self.key, "resource": "obs", "n": 2})
        universal.get_sample_items.invoke({"context_key": self.key, "resource": "obs", "n": 3})
        self.assertEqual(len(get_evidence(self.key)), 2)

    def test_repeated_survey_sweeps_collapse(self):
        """Three players surveying the same context leave one fact per tool."""
        for _ in range(3):
            for tool in tools_for(self.ctx):
                if is_auto_fireable(tool):
                    payload = {"context_key": self.key}
                    if "resource" in tool.args:
                        payload["resource"] = "obs"
                    tool.invoke(payload)
        cited = [e.describe() for e in get_evidence(self.key)]
        self.assertEqual(len(cited), len(set(cited)))  # no duplicates

    def test_cache_is_cleared_with_the_context(self):
        universal.list_resources.invoke({"context_key": self.key})
        self.assertEqual(len(get_evidence(self.key)), 1)

        unregister_context(self.key)
        register_context(self.key, self.ctx)  # same key, fresh run
        universal.list_resources.invoke({"context_key": self.key})
        # A fresh record proves the cache was cleared (a stale hit would record 0).
        self.assertEqual(len(get_evidence(self.key)), 1)


class CallerAttributionTest(unittest.TestCase):
    """Every fact records who fired it and at which step, even under dedup."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "obs.csv")
        pd.DataFrame({"a": [1, 2, 3]}).to_csv(self.csv, index=False)
        self.ctx = create_context(self.csv, name="obs")
        self.key = register_context(f"k_{uuid.uuid4().hex[:8]}", self.ctx)
        clear_evidence()

    def tearDown(self):
        clear_registry()

    def _invoke(self):
        universal.list_resources.invoke({"context_key": self.key})

    def test_producing_call_records_its_caller(self):
        with attributed_to(Caller(agent="player", role="profiler", step=2, phase="survey")):
            self._invoke()
        used_by = get_evidence(self.key)[0].used_by
        self.assertEqual(len(used_by), 1)
        self.assertEqual(
            used_by[0],
            {"agent": "player", "role": "profiler", "step": 2,
             "phase": "survey", "cached": False},
        )

    def test_cache_hit_appends_reuse_by_a_different_caller(self):
        """The same fact, reused by a later step, records the second caller too."""
        with attributed_to(Caller(agent="player", role="profiler", step=0, phase="survey")):
            self._invoke()
        with attributed_to(Caller(agent="player", role="spatial", step=1, phase="survey")):
            self._invoke()  # cache hit: no new entry, but a new use

        entries = get_evidence(self.key)
        self.assertEqual(len(entries), 1)                      # still one fact
        uses = entries[0].used_by
        self.assertEqual([u["cached"] for u in uses], [False, True])
        self.assertEqual([u["role"] for u in uses], ["profiler", "spatial"])
        self.assertEqual([u["step"] for u in uses], [0, 1])

    def test_call_outside_a_scope_is_attributed_to_unknown(self):
        self._invoke()  # no attributed_to scope
        use = get_evidence(self.key)[0].used_by[0]
        self.assertEqual(use["agent"], "unknown")
        self.assertIsNone(use["step"])

    def test_used_by_survives_serialization(self):
        with attributed_to(Caller(agent="orchestrator", phase="inspect")):
            self._invoke()
        row = serialize_evidence(self.key)[0]
        self.assertEqual(row["used_by"][0]["agent"], "orchestrator")
        self.assertEqual(row["used_by"][0]["phase"], "inspect")


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
