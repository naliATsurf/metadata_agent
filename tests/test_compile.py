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

        # Per extraction task: right player, homogeneous tier, short task name.
        for t in extraction:
            bucket = self.bucket_of(fp, t)
            self.assertEqual(t.player, _BUCKET_PLAYER[bucket])
            tiers = {self.tier_of(fp, f) for f in t.fields}
            self.assertEqual(tiers, {t.topology}, "task mixes assurance tiers")

            # `task` is a short action identifier, not a prose instruction.
            self.assertNotIn(" ", t.task)
            self.assertLessEqual(len(t.task), 40)

            # Structured per-field binding: exactly one binding per field, each with
            # its own ranked candidate set and a provisional assurance — the field →
            # candidate binding is data, not prose, and the *only* candidate carrier.
            bound = {b["field"]: b for b in t.field_bindings}
            self.assertEqual(set(bound), set(t.fields))
            for f in t.fields:
                b = bound[f]
                self.assertTrue(b["candidates"], f"no candidates bound for {f}")
                self.assertEqual(b["assurance"], fp.routings[f].assurance)
                self.assertIn("query", b)


# ---------------------------------------------------------------------------
# A realistic, messy bundle: one data table + codebook (with a Kelvin trap),
# three separate narrative documents, a structural field, and an unresolvable one.
# ---------------------------------------------------------------------------


class Survey(BaseModel):
    title: str = Field(description="The title of the survey dataset")
    abstract: Optional[str] = Field(default=None, description="An overview of what the survey records")
    methodology: Optional[str] = Field(default=None, description="How the sampling was carried out")
    funding: Optional[str] = Field(default=None, description="The funding source and grant for the survey")
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
        # One long README — the *usual* shape. Every narrative field lives in its
        # own section of a single document, not in a separate file per field, so the
        # router must localize distinct spans *within* one resource.
        with open(os.path.join(self.dir, "README.md"), "w") as f:
            f.write(
                "# Coastal Rockpool Survey 2021\n\n"
                "## Overview\n\n"
                "The Coastal Rockpool Survey records intertidal species abundance "
                "across stations on the western seaboard, intended for ecological "
                "trend analysis.\n\n"
                "## Methods\n\n"
                "Sampling was carried out by timed searches at each station during "
                "low tide, with two observers per station recording every taxon.\n\n"
                "## Funding\n\n"
                "The survey was funded by the National Marine Research Council under "
                "grant 12-345.\n\n"
                "## Licence\n\n"
                "The dataset is released under the Creative Commons Attribution 4.0 "
                "licence.\n"
            )

        self.tab = create_context(os.path.join(self.dir, "observations.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "codebook.csv"), name="cb")
        self.readme = create_context(os.path.join(self.dir, "README.md"), name="readme")

    def _fp(self) -> FieldPlan:
        catalog = resolve_catalog(self.tab, sources=[self.cb, self.readme])
        return route_fields(Survey, catalog=catalog, docs=[self.readme])

    def _compiled(self):
        fp = self._fp()
        return fp, compile_field_plan(fp)

    def _narrative_fields(self, fp: FieldPlan):
        return [p for p, r in fp.routings.items() if r.bucket == "narrative"]

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

    def test_one_long_document_becomes_one_narrative_task(self):
        """The realistic case: many narrative fields, all in a single README.

        They share (bucket=narrative, resource=README, tier=debate), so the compiler
        groups them into *one* task targeting that document — not a task per field.
        """
        fp, plan = self._compiled()
        narrative = [t for t in self.extraction_tasks(plan) if self.bucket_of(fp, t) == "narrative"]
        self.assertEqual(len(narrative), 1)
        task = narrative[0]
        self.assertEqual(task.target_resources, ["README"])
        self.assertEqual(set(task.fields), set(self._narrative_fields(fp)))

    def test_router_localizes_distinct_spans_within_the_one_document(self):
        """Well-signposted fields resolve to *different, non-overlapping* sections.

        This is the capability that matters for a single long document: the router
        returns a section span, not the whole file, and a different one per field.
        ``title`` is deliberately excluded — it is the classic low-signal field (its
        text rarely contains the word "title"), so it can collide with another
        section; that is the documented lexical ceiling, not a compiler concern.
        """
        fp = self._fp()
        spans = []
        for field in ("abstract", "methodology", "funding"):
            cand = fp.routings[field].candidates[0]
            self.assertEqual(cand.resource, "README")
            self.assertEqual(cand.kind, "quoted_span")
            start, end = cand.locator
            self.assertTrue(0 <= start < end)
            spans.append((start, end))
        # Distinct and pairwise non-overlapping — genuine per-section localization.
        self.assertEqual(len(set(spans)), len(spans))
        for earlier, later in zip(sorted(spans), sorted(spans)[1:]):
            self.assertLessEqual(earlier[1], later[0])  # earlier end <= later start

    def test_binding_preserves_an_under_ranked_but_correct_candidate(self):
        """The point of deferring selection: a right answer the router ranked below
        #1 is still in the field's binding, so the executor can recover it.

        `title` is the low-signal field whose top lexical hit is wrong (it collides
        with the Licence section). The compiler must NOT commit to that top pick — it
        must hand the executor the ranked set, which still contains the real title
        (the `# Coastal Rockpool Survey 2021` H1). Recall, not precision@1.
        """
        _, plan = self._compiled()
        narrative = next(t for t in plan.steps if "title" in t.fields)
        title = next(b for b in narrative.field_bindings if b["field"] == "title")
        # More than one option is offered — selection is genuinely deferred...
        self.assertGreater(len(title["candidates"]), 1)
        # ...and the correct title span is among them, even if not ranked first.
        self.assertTrue(
            any("Coastal Rockpool Survey" in c["snippet"] for c in title["candidates"]),
            "the real title span was dropped — router precision@1 baked in",
        )

    def test_assurance_on_a_binding_is_the_router_grade_and_provisional(self):
        fp, plan = self._compiled()
        col = next(t for t in plan.steps if "water_temperature" in t.fields)
        wt = next(b for b in col.field_bindings if b["field"] == "water_temperature")
        # Provisional == the router's grade, carried for the verifier to confirm.
        self.assertEqual(wt["assurance"], fp.routings["water_temperature"].assurance)
        self.assertEqual(wt["assurance"], "low")

    def test_per_field_scores_are_that_field_s_own_ranking(self):
        """Bindings are per-field, so a shared column carries each field's own score,
        not whichever field happened to be seen first (the old pooled-dedup bug)."""
        _, plan = self._compiled()
        col = next(t for t in plan.steps if "min_latitude" in t.fields and "max_longitude" in t.fields)
        for field, want in (("min_latitude", "lat"), ("max_longitude", "lon")):
            b = next(x for x in col.field_bindings if x["field"] == field)
            self.assertEqual(b["candidates"][0]["locator"], want)  # this field's own top pick

    def test_tight_budget_splits_the_single_document_group_without_leaving_it(self):
        """A budget cap fragments one document's narrative task, but every piece
        still targets that same document and no field is lost."""
        fp = self._fp()
        narrative_fields = self._narrative_fields(fp)
        plan = compile_field_plan(fp, budget=1)
        self.assert_well_formed(fp, plan, Survey)
        narrative = [t for t in self.extraction_tasks(plan) if self.bucket_of(fp, t) == "narrative"]
        self.assertGreater(len(narrative), 1)  # actually split
        self.assertTrue(all(t.target_resources == ["README"] for t in narrative))
        carried = [f for t in narrative for f in t.fields]
        self.assertEqual(sorted(carried), sorted(narrative_fields))

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
        cands = [c for b in lic.field_bindings for c in b["candidates"]]
        self.assertTrue(cands)
        self.assertTrue(all(c["kind"] == "quoted_span" for c in cands))

    def test_determinism_over_the_realistic_bundle(self):
        fp = self._fp()
        self.assertEqual(
            compile_field_plan(fp).model_dump(), compile_field_plan(fp).model_dump()
        )


class Narrative(BaseModel):
    overview: Optional[str] = Field(default=None, description="an overview of what the dataset records")
    methodology: Optional[str] = Field(default=None, description="how the sampling was carried out")
    license: Optional[str] = Field(default=None, description="the licence under which the data is released")


class MultiDocumentTest(CompilerCase):
    """The other real shape: narrative info split across a few separate files.

    Complements RealisticCompileTest (one long document): here fields land in
    *different* documents, and the compiler must group by resource — one task per
    document that actually answers something, not one giant narrative task.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({"x": [1, 2, 3]}).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        with open(os.path.join(self.dir, "README.md"), "w") as f:
            f.write(
                "# Rockpool Survey\n\n## Overview\n\nThe survey records intertidal "
                "species abundance.\n\n## Methods\n\nSampling used timed searches at "
                "low tide.\n"
            )
        with open(os.path.join(self.dir, "LICENSE.md"), "w") as f:
            f.write("# Licence\n\nReleased under the Open Data Commons Attribution licence.\n")
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.readme = create_context(os.path.join(self.dir, "README.md"), name="readme")
        self.license = create_context(os.path.join(self.dir, "LICENSE.md"), name="license")

    def _compiled(self):
        fp = route_fields(Narrative, catalog=resolve_catalog(self.tab), docs=[self.readme, self.license])
        return fp, compile_field_plan(fp)

    def test_fields_split_across_documents_yield_per_document_tasks(self):
        fp, plan = self._compiled()
        self.assert_well_formed(fp, plan, Narrative)
        narrative = [t for t in self.extraction_tasks(plan) if self.bucket_of(fp, t) == "narrative"]
        # license lives in LICENSE.md; overview/methods in README.md → two documents,
        # so two narrative tasks, each targeting exactly one resource.
        targets = {tuple(t.target_resources) for t in narrative}
        self.assertIn(("LICENSE",), targets)
        self.assertIn(("README",), targets)
        self.assertTrue(all(len(t.target_resources) == 1 for t in narrative))

    def test_license_routes_to_its_own_file(self):
        fp, _ = self._compiled()
        self.assertEqual(fp.routings["license"].candidates[0].resource, "LICENSE")


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
            payload = sum(
                len(c["snippet"] or "") for b in t.field_bindings for c in b["candidates"]
            )
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