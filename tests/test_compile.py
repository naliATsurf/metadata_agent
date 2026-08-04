"""Tests for the FieldPlan → Plan compiler (M4, layer 5).

The compiler is deterministic and LLM-free: it lays out execution from a routing
artifact. Rather than assert one happy path, these tests drive the compiler with
realistic, messy bundles — multiple documents, a contested column, unresolved
fields, degenerate schemas, tight budgets — and check the *invariants* that must
hold for every plan it emits:

- **Linchpin 1** — every routed field lands on exactly one extraction task, and the
  assembly task names *every* schema field (so nothing is silently dropped and the
  unresolved ones are explicitly nulled).
- **Grouping & tiering** — fields sharing an extractor group together, but a
  high-assurance field is never dragged into a contested sibling's debate.
- **Budgeting** — grouping never re-creates the flood; over-budget groups split, and
  no field is dropped even when it alone exceeds the budget.
- **A valid, executable Plan** — extraction fans in to one assembly task whose every
  input is produced by an earlier step.

The assertions target these invariants, not the fuzzy routing decisions upstream,
so they stay robust to the documented lexical-ceiling quirks in the router.
"""

import os
import sys
import tempfile
import unittest
from typing import List, Optional

import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.core.constants import DEFAULT_WORKSPACE_ARTIFACTS
from src.core.schemas import Plan, Task
from src.router import FieldPlan, compile_field_plan, resolve_catalog, route_fields
from src.router.compile import _BUCKET_PLAYER, _FINAL_ARTIFACT
from src.router.schema import walk_schema
from src.tools.base import clear_registry


# ---------------------------------------------------------------------------
# Shared invariant checks — every emitted plan must satisfy these, whatever the
# bundle. Written once, reused by every test class below.
# ---------------------------------------------------------------------------


class CompilerCase(unittest.TestCase):
    def tearDown(self):
        clear_registry()

    def extraction_tasks(self, plan: Plan) -> List[Task]:
        return [t for t in plan.steps if t.outputs != [_FINAL_ARTIFACT]]

    def assembly(self, plan: Plan) -> Task:
        return plan.steps[-1]

    def bucket_of(self, fp: FieldPlan, task: Task) -> str:
        """The (single) routing bucket shared by all a task's fields."""
        buckets = {fp.routings[f].bucket for f in task.fields}
        self.assertEqual(len(buckets), 1, f"task mixes buckets: {buckets}")
        return buckets.pop()

    def tier_of(self, fp: FieldPlan, field: str) -> str:
        return "single" if fp.routings[field].assurance == "high" else "debate"

    def assert_well_formed(self, fp: FieldPlan, plan: Plan, schema) -> None:
        """The invariants that must hold for any compiled plan."""
        schema_fields = [s.path for s in walk_schema(schema)]
        routed = [p for p, r in fp.routings.items() if r.status != "unresolved"]
        extraction = self.extraction_tasks(plan)
        assembly = self.assembly(plan)

        # Exactly one assembly task, it is last, and it is the only final producer.
        self.assertIs(assembly, plan.steps[-1])
        finals = [t for t in plan.steps if t.outputs == [_FINAL_ARTIFACT]]
        self.assertEqual(len(finals), 1)
        self.assertEqual(assembly.player, "metadata_generator")

        # Linchpin 1: every routed field on exactly one extraction task; none twice.
        carried = [f for t in extraction for f in t.fields]
        self.assertEqual(sorted(carried), sorted(routed))
        self.assertEqual(len(carried), len(set(carried)), "a field is on two tasks")

        # Assembly names *every* schema field (incl. unresolved), in schema order.
        self.assertEqual(assembly.fields, schema_fields)

        # Fan-in: assembly consumes exactly the extraction outputs, keys unique.
        extraction_outputs = [o for t in extraction for o in t.outputs]
        self.assertEqual(len(extraction_outputs), len(set(extraction_outputs)))
        self.assertEqual(sorted(assembly.inputs.values()), sorted(extraction_outputs))
        self.assertEqual(len(assembly.inputs), len(extraction_outputs))

        # Executable dataflow: every input an assembly reads is produced earlier.
        produced = set(DEFAULT_WORKSPACE_ARTIFACTS)
        for step in plan.steps[:-1]:
            for need in step.inputs.values():
                self.assertIn(need, produced)
            produced.update(step.outputs)
        for need in assembly.inputs.values():
            self.assertIn(need, produced)

        # Per extraction task: right player, homogeneous tier, deduped candidates.
        for t in extraction:
            bucket = self.bucket_of(fp, t)
            self.assertEqual(t.player, _BUCKET_PLAYER[bucket])
            tiers = {self.tier_of(fp, f) for f in t.fields}
            self.assertEqual(tiers, {t.topology}, "task mixes assurance tiers")
            keys = [(c["resource"], c["locator"], c["kind"]) for c in t.candidates]
            self.assertEqual(len(keys), len(set(keys)), "duplicate seeded candidate")


# ---------------------------------------------------------------------------
# A realistic, messy bundle: one data table + codebook (with a Kelvin trap),
# three separate narrative documents, a structural field, and an unresolvable one.
# ---------------------------------------------------------------------------


class Survey(BaseModel):
    title: str = Field(description="The title of the survey dataset")
    methodology: Optional[str] = Field(default=None, description="How the sampling was carried out")
    license: Optional[str] = Field(default=None, description="The licence under which the data is released")
    min_latitude: Optional[float] = Field(default=None, description="the southernmost latitude sampled")
    max_longitude: Optional[float] = Field(default=None, description="the easternmost longitude sampled")
    water_temperature: Optional[str] = Field(default=None, description="the water temperature measured at each station")
    row_count: Optional[int] = Field(default=None, description="the number of rows in the observations table")
    provenance_notes: Optional[str] = Field(default=None, description="zzzq wxyv unmatchable gibberish")


class RealisticCompileTest(CompilerCase):
    """Multi-document, contested-column, unresolved-field bundle."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({
            "station_id": list(range(1, 9)),
            "lat": [50.1, 50.5, 51.0, 51.4, 52.0, 52.6, 53.1, 53.8],
            "lon": [-9.5, -9.1, -8.7, -8.2, -7.8, -7.3, -6.9, -6.4],
            "water_temp": [11.2, 12.5, 10.8, 13.1, 12.0, 11.7, 13.4, 12.9],
        }).to_csv(os.path.join(self.dir, "observations.csv"), index=False)
        pd.DataFrame({
            "variable": ["station_id", "lat", "lon", "water_temp"],
            "description": [
                "Monitoring station identifier",
                "Latitude in decimal degrees",
                "Longitude in decimal degrees",
                "Water temperature at the station",
            ],
            "units": ["", "degrees", "degrees", "Kelvin"],  # planted stale-units trap
        }).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)
        for name, body in {
            "README.md": "# Rockpool Survey\n\nThe Rockpool Survey records intertidal species abundance.\n",
            "METHODS.md": "# Methods\n\nSampling was carried out by timed searches at low tide.\n",
            "LICENSE.md": "# Licence\n\nReleased under the Open Data Commons Attribution licence.\n",
        }.items():
            with open(os.path.join(self.dir, name), "w") as f:
                f.write(body)

        self.tab = create_context(os.path.join(self.dir, "observations.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "codebook.csv"), name="cb")
        self.readme = create_context(os.path.join(self.dir, "README.md"), name="readme")
        self.methods = create_context(os.path.join(self.dir, "METHODS.md"), name="methods")
        self.license = create_context(os.path.join(self.dir, "LICENSE.md"), name="license")

    def _fp(self) -> FieldPlan:
        catalog = resolve_catalog(self.tab, sources=[self.cb, self.readme, self.methods, self.license])
        return route_fields(Survey, catalog=catalog, docs=[self.readme, self.methods, self.license])

    def _compiled(self):
        fp = self._fp()
        return fp, compile_field_plan(fp)

    def test_plan_is_well_formed(self):
        fp, plan = self._compiled()
        self.assert_well_formed(fp, plan, Survey)

    def test_unresolved_field_skipped_from_extraction_but_nulled_at_assembly(self):
        fp, plan = self._compiled()
        self.assertEqual(fp.routings["provenance_notes"].status, "unresolved")
        self.assertNotIn(
            "provenance_notes", {f for t in self.extraction_tasks(plan) for f in t.fields}
        )
        self.assertIn("provenance_notes", self.assembly(plan).fields)

    def test_each_narrative_document_becomes_its_own_task(self):
        """title/methodology/license resolve to three *different* docs → three tasks."""
        fp, plan = self._compiled()
        narrative = [t for t in self.extraction_tasks(plan) if self.bucket_of(fp, t) == "narrative"]
        targets = [tuple(t.target_resources) for t in narrative]
        self.assertEqual(len(narrative), len(set(targets)), "docs collapsed into one task")
        # Each narrative task carries exactly one field here (one field per doc).
        self.assertTrue(all(len(t.target_resources) == 1 for t in narrative))

    def test_contested_column_does_not_drag_high_assurance_siblings_into_debate(self):
        """water_temp is refuted (Kelvin trap) → its own debate task; lat/lon stay single."""
        fp, plan = self._compiled()
        self.assertEqual(fp.routings["water_temperature"].assurance, "low")
        wt_task = next(t for t in plan.steps if "water_temperature" in t.fields)
        self.assertEqual(wt_task.topology, "debate")
        # The high-assurance coordinates are elsewhere, and in a single-extractor task.
        for hi in ("min_latitude", "max_longitude"):
            hi_task = next(t for t in plan.steps if hi in t.fields)
            self.assertEqual(hi_task.topology, "single")
            self.assertNotIn("water_temperature", hi_task.fields)

    def test_structural_field_is_context_level(self):
        fp, plan = self._compiled()
        rc_task = next(t for t in plan.steps if "row_count" in t.fields)
        self.assertEqual(self.bucket_of(fp, rc_task), "structural")
        self.assertEqual(rc_task.target_resources, [])  # whole-context tool
        self.assertEqual(rc_task.topology, "single")

    def test_narrative_task_seeds_a_quoted_span_not_a_column(self):
        _, plan = self._compiled()
        lic = next(t for t in plan.steps if "license" in t.fields)
        self.assertTrue(lic.candidates)
        self.assertTrue(all(c["kind"] == "quoted_span" for c in lic.candidates))

    def test_determinism_over_the_realistic_bundle(self):
        fp = self._fp()
        self.assertEqual(
            compile_field_plan(fp).model_dump(), compile_field_plan(fp).model_dump()
        )


# ---------------------------------------------------------------------------
# Budgeting edge cases.
# ---------------------------------------------------------------------------


class Geo(BaseModel):
    min_lat: Optional[float] = Field(default=None, description="the southernmost latitude sampled")
    max_lat: Optional[float] = Field(default=None, description="the northernmost latitude sampled")
    min_lon: Optional[float] = Field(default=None, description="the westernmost longitude sampled")
    max_lon: Optional[float] = Field(default=None, description="the easternmost longitude sampled")


class BudgetTest(CompilerCase):
    """Several high-assurance columns in one group — split only by budget."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({
            "lat": [50.1, 50.5, 51.0], "lon": [-9.5, -9.1, -8.7],
        }).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        # A codebook that describes lat/lon for both the min and max fields. Long
        # descriptions so per-field payload is chunky enough to split under a budget.
        pd.DataFrame({
            "variable": ["lat", "lon"],
            "description": [
                "Latitude of the sampling location in decimal degrees north of the equator",
                "Longitude of the sampling location in decimal degrees east of the meridian",
            ],
        }).to_csv(os.path.join(self.dir, "cb.csv"), index=False)
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")

    def _fp(self):
        return route_fields(Geo, catalog=resolve_catalog(self.tab, sources=[self.cb]), docs=[])

    def test_generous_budget_keeps_the_group_together(self):
        fp = self._fp()
        plan = compile_field_plan(fp, budget=10_000)
        self.assert_well_formed(fp, plan, Geo)
        col_tasks = [t for t in self.extraction_tasks(plan) if self.bucket_of(fp, t) == "ambiguous_structural"]
        # All same-tier columns from the same resource → a single task.
        self.assertEqual(len(col_tasks), 1)

    def test_tiny_budget_splits_but_drops_nothing(self):
        fp = self._fp()
        routed = [p for p, r in fp.routings.items() if r.status != "unresolved"]
        plan = compile_field_plan(fp, budget=1)
        self.assert_well_formed(fp, plan, Geo)  # still valid, just more tasks
        extraction = self.extraction_tasks(plan)
        # A 1-char budget forces one field per task; no field lost.
        self.assertEqual(len(extraction), len(routed))
        self.assertTrue(all(len(t.fields) == 1 for t in extraction))

    def test_multi_field_task_payload_stays_within_budget(self):
        # Derive a budget that admits exactly the first two fields of the group, so
        # the test forces a real split rather than guessing a magic number.
        from src.router.compile import _candidate_cost

        fp = self._fp()
        group = sorted(
            (r for r in fp.routings.values() if r.bucket == "ambiguous_structural"),
            key=lambda r: r.field_path,
        )
        self.assertGreaterEqual(len(group), 2)
        budget = _candidate_cost(group[0]) + _candidate_cost(group[1])

        plan = compile_field_plan(fp, budget=budget)
        self.assert_well_formed(fp, plan, Geo)
        multi = [t for t in self.extraction_tasks(plan) if len(t.fields) > 1]
        self.assertTrue(multi, "budget did not produce a grouped task to check")
        for t in multi:
            payload = sum(len(c["snippet"] or "") for c in t.candidates)
            self.assertLessEqual(payload, budget)

    def test_field_larger_than_budget_still_gets_its_own_task(self):
        # Budget below a single field's payload: it cannot be split further, so it
        # must still be emitted alone rather than dropped.
        fp = self._fp()
        plan = compile_field_plan(fp, budget=1)
        carried = [f for t in self.extraction_tasks(plan) for f in t.fields]
        routed = [p for p, r in fp.routings.items() if r.status != "unresolved"]
        self.assertEqual(sorted(carried), sorted(routed))


# ---------------------------------------------------------------------------
# Degenerate schemas.
# ---------------------------------------------------------------------------


class AllUnresolved(BaseModel):
    a: Optional[str] = Field(default=None, description="qzzx wvvq blorp")
    b: Optional[str] = Field(default=None, description="frob nicate grault")


class OneField(BaseModel):
    title: str = Field(description="the title of the dataset")


class DegenerateTest(CompilerCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({"x": [1, 2, 3]}).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        with open(os.path.join(self.dir, "doc.md"), "w") as f:
            f.write("# Dataset\n\nThe dataset title is Foo Survey.\n")
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.doc = create_context(os.path.join(self.dir, "doc.md"), name="doc")

    def test_all_unresolved_yields_only_the_assembly_task(self):
        fp = route_fields(AllUnresolved, catalog=resolve_catalog(self.tab), docs=[self.doc])
        plan = compile_field_plan(fp)
        self.assertEqual(len(plan.steps), 1)
        assembly = plan.steps[0]
        self.assertEqual(assembly.outputs, [_FINAL_ARTIFACT])
        self.assertEqual(assembly.inputs, {})            # nothing to fan in
        self.assertEqual(assembly.fields, ["a", "b"])    # both still named for nulling
        self.assert_well_formed(fp, plan, AllUnresolved)

    def test_single_field_bundle_is_valid(self):
        fp = route_fields(OneField, catalog=resolve_catalog(self.tab), docs=[self.doc])
        plan = compile_field_plan(fp)
        self.assert_well_formed(fp, plan, OneField)
        self.assertEqual(len(self.extraction_tasks(plan)), 1)


# ---------------------------------------------------------------------------
# Caller-facing knobs: player overrides.
# ---------------------------------------------------------------------------


class OverrideTest(CompilerCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({"la": [53.1, 53.2, 53.3]}).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        pd.DataFrame({"variable": ["la"], "label": ["Latitude of the point"]}).to_csv(
            os.path.join(self.dir, "cb.csv"), index=False
        )
        with open(os.path.join(self.dir, "doc.md"), "w") as f:
            f.write("# Dataset\n\nThe dataset title is Foo Survey.\n")
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        self.doc = create_context(os.path.join(self.dir, "doc.md"), name="doc")

    class Meta(BaseModel):
        title: str = Field(description="the title of the dataset")
        lat_field: Optional[float] = Field(default=None, description="the latitude coordinate column")

    def _fp(self):
        return route_fields(self.Meta, catalog=resolve_catalog(self.tab, sources=[self.cb]), docs=[self.doc])

    def test_custom_bucket_player_and_assembly_player(self):
        fp = self._fp()
        plan = compile_field_plan(
            fp,
            bucket_player={"narrative": "critic"},
            assembly_player="schema_expert",
        )
        self.assertEqual(self.assembly(plan).player, "schema_expert")
        narrative = next(t for t in plan.steps if "title" in t.fields)
        self.assertEqual(narrative.player, "critic")
        # Unspecified buckets keep their default.
        col = next(t for t in plan.steps if "lat_field" in t.fields)
        self.assertEqual(col.player, _BUCKET_PLAYER["ambiguous_structural"])


if __name__ == "__main__":
    unittest.main()