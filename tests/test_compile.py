"""Tests for the FieldPlan → Plan compiler (M4, layer 5).

The compiler is deterministic and LLM-free: it lays out execution from a routing
artifact. These tests assert the linchpin (field identity survives), the grouping
and budgeting, per-task topology by assurance, the fan-in assembly task, and that
unresolved fields are skipped-but-nulled — without running any player.
"""

import os
import sys
import tempfile
import unittest
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.core.schemas import Plan
from src.router import compile_field_plan, resolve_catalog, route_fields
from src.router.compile import _FINAL_ARTIFACT
from src.tools.base import clear_registry


class Meta(BaseModel):
    title: str = Field(description="The title of the dataset")
    record_count: Optional[int] = Field(default=None, description="The number of records in the data")
    lat_field: Optional[float] = Field(default=None, description="the latitude coordinate column")
    mystery: Optional[str] = Field(default=None, description="qzzx wvvq blorp")


class CompileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({"la": [53.1, 53.2, 53.3]}).to_csv(
            os.path.join(self.dir, "obs.csv"), index=False
        )
        pd.DataFrame({"variable": ["la"], "label": ["Latitude of the point"]}).to_csv(
            os.path.join(self.dir, "cb.csv"), index=False
        )
        with open(os.path.join(self.dir, "doc.md"), "w") as f:
            f.write("# Dataset\n\nThe dataset title is Foo Survey.\n\nCollected by a team.\n")
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        self.doc = create_context(os.path.join(self.dir, "doc.md"), name="doc")

    def tearDown(self):
        clear_registry()

    def _plan(self):
        catalog = resolve_catalog(self.tab, sources=[self.cb])
        return route_fields(Meta, catalog=catalog, docs=[self.doc])

    def _compiled(self):
        return compile_field_plan(self._plan())

    # --- shape ---

    def test_compiles_to_a_plan(self):
        self.assertIsInstance(self._compiled(), Plan)

    def test_last_task_is_the_assembly_fan_in(self):
        plan = self._compiled()
        assembly = plan.steps[-1]
        self.assertEqual(assembly.player, "metadata_generator")
        self.assertEqual(assembly.outputs, [_FINAL_ARTIFACT])

    def test_assembly_depends_on_every_extraction_output(self):
        plan = self._compiled()
        extraction_outputs = [o for t in plan.steps[:-1] for o in t.outputs]
        assembly = plan.steps[-1]
        self.assertEqual(sorted(assembly.inputs.values()), sorted(extraction_outputs))
        self.assertTrue(extraction_outputs)  # there is real extraction to fan in

    # --- linchpin 1: field identity survives ---

    def test_every_routed_field_is_carried_on_some_task(self):
        plan = self._compiled()
        carried = {f for t in plan.steps[:-1] for f in t.fields}
        # title (narrative), record_count (structural), lat_field (column) route;
        # mystery is unresolved and gets no extraction task.
        self.assertEqual(carried, {"title", "record_count", "lat_field"})

    def test_unresolved_field_has_no_extraction_task_but_is_named_for_assembly(self):
        plan = self._compiled()
        extraction_fields = {f for t in plan.steps[:-1] for f in t.fields}
        self.assertNotIn("mystery", extraction_fields)
        # ...yet assembly names it, so the record nulls it explicitly.
        self.assertIn("mystery", plan.steps[-1].fields)

    # --- grouping, seeding, topology ---

    def test_a_task_seeds_only_its_own_candidates(self):
        plan = self._compiled()
        column_task = next(t for t in plan.steps if "lat_field" in t.fields)
        locators = {c["locator"] for c in column_task.candidates}
        self.assertIn("la", locators)
        self.assertTrue(column_task.candidates)

    def test_high_assurance_group_gets_single_topology(self):
        plan = self._compiled()
        # record_count is a high-assurance structural field.
        structural = next(t for t in plan.steps if "record_count" in t.fields)
        self.assertEqual(structural.topology, "single")

    def test_narrative_group_targets_its_document(self):
        plan = self._compiled()
        narrative = next(t for t in plan.steps if "title" in t.fields)
        self.assertEqual(narrative.player, "metadata_specialist")

    def test_output_artifact_names_are_unique(self):
        plan = self._compiled()
        outputs = [o for t in plan.steps for o in t.outputs]
        self.assertEqual(len(outputs), len(set(outputs)))

    def test_deterministic(self):
        a = compile_field_plan(self._plan()).model_dump()
        b = compile_field_plan(self._plan()).model_dump()
        self.assertEqual(a, b)


class BudgetTest(unittest.TestCase):
    """A tiny budget must split a shared-extractor group into several tasks."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({
            "lat": [50.1, 50.5, 51.0],
            "lon": [-9.5, -9.1, -8.7],
            "obs_date": ["2021-05-01", "2021-05-02", "2021-05-03"],
        }).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        pd.DataFrame({
            "variable": ["lat", "lon", "obs_date"],
            "description": [
                "Latitude in decimal degrees",
                "Longitude in decimal degrees",
                "Date of the observation",
            ],
        }).to_csv(os.path.join(self.dir, "cb.csv"), index=False)
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")

    def tearDown(self):
        clear_registry()

    class Geo(BaseModel):
        min_lat: Optional[float] = Field(default=None, description="the southernmost latitude sampled")
        max_lon: Optional[float] = Field(default=None, description="the easternmost longitude sampled")
        the_date: Optional[str] = Field(default=None, description="the date of observation recorded")

    def _plan(self):
        catalog = resolve_catalog(self.tab, sources=[self.cb])
        return route_fields(self.Geo, catalog=catalog, docs=[])

    def test_budget_splits_one_extractor_group_into_multiple_tasks(self):
        plan = self._plan()
        column_fields = [
            p for p, r in plan.routings.items() if r.bucket == "ambiguous_structural"
        ]
        self.assertGreaterEqual(len(column_fields), 2)  # a real group to split

        generous = compile_field_plan(plan, budget=10_000)
        tiny = compile_field_plan(plan, budget=1)

        # Under a generous budget the shared-extractor fields share one task; under a
        # 1-char budget each field is forced into its own task. No field is dropped.
        generous_tasks = [t for t in generous.steps if t.fields and t.player != "metadata_generator"]
        tiny_tasks = [t for t in tiny.steps if t.fields and t.player != "metadata_generator"]
        self.assertLess(len(generous_tasks), len(tiny_tasks))

        tiny_fields = sorted(f for t in tiny_tasks for f in t.fields)
        self.assertEqual(tiny_fields, sorted(column_fields))


if __name__ == "__main__":
    unittest.main()