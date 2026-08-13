"""Tests for catalog resolution — symbol linking, priors, cross-check (M2, layer 3)."""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.router import (
    CachedProseReader,
    DeterministicProseReader,
    LLMProseReader,
    ProseReader,
    resolve_bundle,
    resolve_catalog,
)
from src.tools.base import clear_registry


class _CountingReader(ProseReader):
    """A deterministic reader that records every batched call, to prove call shape."""

    def __init__(self):
        self._inner = DeterministicProseReader()
        self.calls = []  # one (column_names, chunk_text) per read_many invocation

    def read_many(self, *, columns, chunk):
        self.calls.append(([name for name, _ in columns], chunk))
        return self._inner.read_many(columns=columns, chunk=chunk)


class CatalogResolutionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Opaque-named observation table.
        pd.DataFrame(
            {
                "la": [53.1, 53.2, 53.3],       # latitude (float, [-90,90])
                "lo": [-9.1, -9.2, -9.3],       # longitude (float)
                "dt": ["2020-01-01", "2020-06-01", "2021-01-01"],
                "n": [1, 2, 3],                 # integer count
                "tmp": [4.5, 10.2, 21.0],       # temperature °C
            }
        ).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        # A structured data dictionary keyed by column name; tmp's units lie.
        pd.DataFrame(
            {
                "variable": ["la", "lo", "dt", "n", "tmp"],
                "label": [
                    "Latitude of the point",
                    "Longitude of the point",
                    "Date of observation",
                    "Count of individuals",
                    "Air temperature",
                ],
                "units": ["decimal degrees", "decimal degrees", "ISO date", "count", "Kelvin"],
            }
        ).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)

        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.codebook = create_context(os.path.join(self.dir, "codebook.csv"), name="codebook")

    def tearDown(self):
        clear_registry()

    # --- structured dictionary -------------------------------------------

    def test_structured_dictionary_resolves_opaque_column(self):
        cat = resolve_catalog(self.tab, sources=[self.codebook])
        la = cat.get("la")
        self.assertEqual(la.link_method, "structured_dictionary")
        self.assertEqual(la.link_confidence, "high")
        self.assertIn("Latitude", la.description)
        self.assertIn("la", la.link_evidence)

    def test_units_conflict_is_flagged(self):
        cat = resolve_catalog(self.tab, sources=[self.codebook])
        tmp = cat.get("tmp")
        self.assertTrue(tmp.conflicts)
        self.assertIn("Kelvin", tmp.conflicts[0])
        self.assertIn("tmp:", cat.conflicts[0])

    def test_resolution_closes_the_semantic_gap(self):
        # Before: opaque names defeat the raw search.
        self.assertEqual(self.tab.search("latitude"), [])
        # After: the enriched catalog reaches the column through its description.
        cat = resolve_catalog(self.tab, sources=[self.codebook])
        hits = cat.search("latitude")
        self.assertTrue(hits)
        self.assertEqual(hits[0].locator, "la")

    def test_non_dictionary_tabular_source_is_ignored(self):
        pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(
            os.path.join(self.dir, "other.csv"), index=False
        )
        other = create_context(os.path.join(self.dir, "other.csv"), name="other")
        cat = resolve_catalog(self.tab, sources=[other])
        # No key coverage → not treated as a dictionary → falls back to priors.
        self.assertNotEqual(cat.get("la").link_method, "structured_dictionary")

    # --- value priors (floor) --------------------------------------------

    def test_self_evident_types_resolve_by_value(self):
        """Only coordinate and temporal — the kinds values genuinely identify."""
        cat = resolve_catalog(self.tab)
        self.assertEqual(cat.get("la").link_method, "value_prior")
        self.assertEqual(cat.get("la").value_label, "coordinate")
        self.assertEqual(cat.get("la").link_confidence, "medium")  # ambiguous by value
        self.assertEqual(cat.get("dt").link_method, "value_prior")
        self.assertEqual(cat.get("dt").value_label, "temporal")
        self.assertEqual(cat.get("dt").link_confidence, "high")

    def test_long_tail_numeric_abstains(self):
        """A generic numeric measure has no self-evident meaning → unresolved."""
        pd.DataFrame({"bio": [123.4, 456.7, 789.0]}).to_csv(
            os.path.join(self.dir, "bio.csv"), index=False
        )
        cat = resolve_catalog(create_context(os.path.join(self.dir, "bio.csv"), name="bio"))
        col = cat.get("bio")
        self.assertEqual(col.link_method, "none")     # abstains, not a coordinate
        self.assertIsNone(col.description)
        self.assertEqual(col.value_label, "numeric")  # profile still recorded

    def test_integer_column_is_not_a_coordinate(self):
        cat = resolve_catalog(self.tab)
        self.assertEqual(cat.get("n").value_label, "numeric")
        self.assertEqual(cat.get("n").link_method, "none")  # abstains

    # --- lexical prose ----------------------------------------------------

    def test_lexical_prose_definition_resolves_a_column(self):
        pd.DataFrame({"qq": [1, 2, 3]}).to_csv(os.path.join(self.dir, "q.csv"), index=False)
        with open(os.path.join(self.dir, "notes.md"), "w") as f:
            f.write("# Notes\n\nHere qq = quality quotient score for the site.\n")
        tab = create_context(os.path.join(self.dir, "q.csv"), name="q")
        doc = create_context(os.path.join(self.dir, "notes.md"), name="notes")
        cat = resolve_catalog(tab, sources=[doc])
        qq = cat.get("qq")
        self.assertEqual(qq.link_method, "lexical_prose")
        self.assertIn("quality quotient", qq.description)

    # --- cross-check without a dictionary claim --------------------------

    def test_out_of_range_latitude_claim_conflicts(self):
        pd.DataFrame({"x": [100.0, 150.0, 200.0]}).to_csv(
            os.path.join(self.dir, "bad.csv"), index=False
        )
        pd.DataFrame({"variable": ["x"], "label": ["Latitude"], "units": ["degrees"]}).to_csv(
            os.path.join(self.dir, "bad_codebook.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "bad.csv"), name="bad")
        cb = create_context(os.path.join(self.dir, "bad_codebook.csv"), name="bad_cb")
        cat = resolve_catalog(tab, sources=[cb])
        self.assertTrue(cat.get("x").conflicts)
        self.assertIn("[-90, 90]", cat.get("x").conflicts[0])


class CatalogEdgeCaseTest(unittest.TestCase):
    """Realistic messiness: partial codebooks, decoys, conflicts, unchecked claims."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame(
            {
                "la": [53.1, 53.2, 53.3],
                "lo": [-9.1, -9.2, -9.3],
                "sp": ["AA", "BB", "CC"],
                "n": [1, 2, 3],
                "depth": [10.0, 20.0, 30.0],
            }
        ).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")

    def tearDown(self):
        clear_registry()

    def _codebook(self, name, mapping, extra_units=None):
        """A codebook CSV: {variable -> description}."""
        rows = {"variable": list(mapping), "description": list(mapping.values())}
        if extra_units:
            rows["units"] = [extra_units.get(v, "") for v in mapping]
        path = os.path.join(self.dir, f"{name}.csv")
        pd.DataFrame(rows).to_csv(path, index=False)
        return create_context(path, name=name)

    def test_partial_codebook_resolves_only_what_it_covers(self):
        """The cliff fix: a codebook covering 2 of 5 columns still contributes.

        Under a recall threshold this whole source (40% coverage) was discarded;
        keying on the key column's precision keeps its rows.
        """
        cb = self._codebook("cb", {"la": "Latitude", "lo": "Longitude"})
        cat = resolve_catalog(self.tab, sources=[cb])
        self.assertEqual(cat.get("la").link_method, "structured_dictionary")
        self.assertEqual(cat.get("lo").link_method, "structured_dictionary")
        # Columns the codebook omits fall through to the other rungs.
        self.assertNotEqual(cat.get("sp").link_method, "structured_dictionary")
        self.assertNotEqual(cat.get("n").link_method, "structured_dictionary")

    def test_data_table_with_a_coincidental_name_is_not_a_codebook(self):
        """A key column mostly of non-names (low precision) is rejected as a decoy."""
        pd.DataFrame(
            {"note": ["la", "x1", "x2", "x3", "x4", "x5"], "val": list(range(6))}
        ).to_csv(os.path.join(self.dir, "decoy.csv"), index=False)
        decoy = create_context(os.path.join(self.dir, "decoy.csv"), name="decoy")
        cat = resolve_catalog(self.tab, sources=[decoy])
        self.assertNotEqual(cat.get("la").link_method, "structured_dictionary")

    def test_conflicting_codebooks_are_surfaced_not_silently_dropped(self):
        """Disagreeing sources: the conflict is recorded and confidence lowered."""
        cb1 = self._codebook("cb1", {"la": "Latitude", "lo": "Longitude"})
        cb2 = self._codebook("cb2", {"la": "Something else", "lo": "Other"})
        cat = resolve_catalog(self.tab, sources=[cb1, cb2])
        la = cat.get("la")
        self.assertIn("Latitude", la.description)          # first consistent, still chosen
        self.assertTrue(la.conflicts)                      # disagreement surfaced
        self.assertEqual(la.link_confidence, "medium")     # not "high" — contested
        self.assertEqual(la.corroborated_by, [])           # they disagree — no corroboration
        # the losing candidate is kept, not discarded
        self.assertTrue(any("Something else" in a["description"] for a in la.alternatives))

    def test_value_profile_adjudicates_a_unit_conflict(self):
        """Two codebooks disagree on units; the values break the tie."""
        pd.DataFrame({"temp": [4.5, 10.2, 21.0]}).to_csv(
            os.path.join(self.dir, "t.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "t.csv"), name="t")
        # Kelvin is listed *first*, but 4–21 refutes it → Celsius wins.
        cbk = self._codebook("cbk", {"temp": "Air temperature"}, extra_units={"temp": "Kelvin"})
        cbc = self._codebook("cbc", {"temp": "Air temperature"}, extra_units={"temp": "Celsius"})
        cat = resolve_catalog(tab, sources=[cbk, cbc])
        temp = cat.get("temp")
        self.assertEqual(temp.units, "Celsius")            # adjudicated by the values
        self.assertEqual(temp.link_confidence, "medium")
        self.assertTrue(any("Kelvin" in c for c in temp.conflicts))

    def test_corroborating_prose_raises_confidence(self):
        """Two documents defining the same token → corroborated above single-source."""
        pd.DataFrame({"qq": [1, 2, 3]}).to_csv(os.path.join(self.dir, "q.csv"), index=False)
        for i in (1, 2):
            with open(os.path.join(self.dir, f"doc{i}.md"), "w") as f:
                f.write(f"# Doc {i}\n\nqq = quality index\n")
        tab = create_context(os.path.join(self.dir, "q.csv"), name="q")
        d1 = create_context(os.path.join(self.dir, "doc1.md"), name="doc1")
        d2 = create_context(os.path.join(self.dir, "doc2.md"), name="doc2")
        qq = resolve_catalog(tab, sources=[d1, d2]).get("qq")
        self.assertEqual(qq.link_method, "lexical_prose")
        self.assertEqual(qq.link_confidence, "high")       # corroborated (a single prose is medium)
        self.assertEqual(qq.conflicts, [])
        # the agreeing source is recorded, citably — not just a confidence bump
        self.assertEqual(len(qq.corroborated_by), 1)
        self.assertIn("doc2", qq.corroborated_by[0])

    def test_corroboration_is_recorded_with_citations(self):
        """Two codebooks agreeing verbatim: the confirmer is cited, not just counted."""
        cb1 = self._codebook("cb1", {"la": "Latitude", "lo": "Longitude"})
        cb2 = self._codebook("cb2", {"la": "Latitude", "lo": "Longitude"})
        la = resolve_catalog(self.tab, sources=[cb1, cb2]).get("la")
        self.assertEqual(la.link_confidence, "high")       # corroborated
        self.assertEqual(la.conflicts, [])                 # agreement, not conflict
        self.assertEqual(len(la.corroborated_by), 1)       # the second codebook
        self.assertIn("cb2", la.corroborated_by[0])

    def test_wrong_categorical_description_is_not_cross_checked(self):
        """KNOWN LIMITATION: cross-check is numeric-only, so a wrong categorical
        meaning is accepted with no conflict."""
        cb = self._codebook("cb", {"sp": "Site name", "n": "Count"})
        cat = resolve_catalog(self.tab, sources=[cb])
        sp = cat.get("sp")
        self.assertEqual(sp.description, "Site name")   # accepted verbatim
        self.assertEqual(sp.conflicts, [])              # numeric cross-check can't catch it

    def test_full_codebook_still_resolves_everything(self):
        """The happy path is unchanged by the precision-based acceptance."""
        cb = self._codebook(
            "cb",
            {"la": "Latitude", "lo": "Longitude", "sp": "Species", "n": "Count", "depth": "Depth"},
        )
        cat = resolve_catalog(self.tab, sources=[cb])
        self.assertTrue(all(c.link_method == "structured_dictionary" for c in cat.columns))


class FullyExercisedCatalogTest(unittest.TestCase):
    """One bundle that drives every resolution outcome at once.

    The columns are chosen so that, between them, every `link_method`, every
    `link_confidence`, every `value_label`, and each of conflicts / corroboration /
    alternatives is exercised — and one column (`tmp`) fills *all* of them at once:

    | column | method               | conf   | label       | fills                              |
    |--------|----------------------|--------|-------------|------------------------------------|
    | tmp    | structured_dictionary| medium | numeric     | everything (conflict+corrob+alts)  |
    | la     | structured_dictionary| high   | coordinate  | corroboration → high, alternatives |
    | frac   | structured_dictionary| low    | numeric     | chosen claim refuted by values     |
    | note   | lexical_prose        | medium | categorical | prose definition                   |
    | dt     | value_prior          | high   | temporal    | self-evident value, chosen by prior|
    | oid    | none                 | none   | numeric     | abstains — nothing describes it     |

    `tmp`: two codebooks agree on Celsius (corroboration) while a third says Kelvin,
    which the 4.1–21.9 values refute (a same-tier rival ruled out → medium, conflict
    recorded); all three plus the value prior are kept as alternatives.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame(
            {
                "oid": list(range(1, 9)),
                "la": [53.1, 53.4, 53.8, 54.0, 54.2, 54.5, 54.7, 54.8],
                "tmp": [4.1, 8.3, 12.0, 15.5, 18.2, 21.9, 10.4, 6.7],
                "note": ["ok", "ok", "flag", "ok", "flag", "ok", "ok", "flag"],
                "dt": ["2021-03-%02d" % (i + 1) for i in range(8)],
                "frac": [0, 40, 120, 250, 15, 90, 300, 5],  # a codebook wrongly calls this a %
            }
        ).to_csv(os.path.join(self.dir, "observations.csv"), index=False)
        # cb1 & cb2 agree on tmp (Celsius) → corroboration; cb3 says Kelvin → refuted.
        # cb1 also (wrongly) labels frac a percentage, refuted by its 0–300 range.
        pd.DataFrame({
            "variable": ["tmp", "la", "frac"],
            "description": ["Water temperature at the station", "Latitude of the survey point", "Fraction measure"],
            "units": ["Celsius", "decimal degrees", "%"],
        }).to_csv(os.path.join(self.dir, "cb1.csv"), index=False)
        pd.DataFrame({
            "variable": ["tmp", "la"],
            "description": ["Water temperature at the station", "Latitude of the survey point"],
            "units": ["Celsius", "decimal degrees"],
        }).to_csv(os.path.join(self.dir, "cb2.csv"), index=False)
        pd.DataFrame({
            "variable": ["tmp"],
            "description": ["Water temperature at the station"],
            "units": ["Kelvin"],
        }).to_csv(os.path.join(self.dir, "cb3.csv"), index=False)
        with open(os.path.join(self.dir, "README.md"), "w") as f:
            f.write("# Survey\n\nField glossary: `note` = field remark recorded by the observer.\n")

        self.tab = create_context(os.path.join(self.dir, "observations.csv"), name="obs")
        self.sources = [
            create_context(os.path.join(self.dir, n), name=n.split(".")[0])
            for n in ("cb1.csv", "cb2.csv", "cb3.csv", "README.md")
        ]

    def tearDown(self):
        clear_registry()

    def _catalog(self):
        return resolve_catalog(self.tab, sources=self.sources)

    def test_tmp_column_populates_every_field(self):
        """The linchpin: a single column with *no* empty ResolvedColumn field."""
        tmp = self._catalog().get("tmp")
        # Scalar fields all set (non-empty, non-"none").
        self.assertEqual(tmp.resource, "observations")
        self.assertEqual(tmp.name, "tmp")
        self.assertTrue(tmp.dtype)
        self.assertEqual(tmp.description, "Water temperature at the station")
        self.assertEqual(tmp.units, "Celsius")
        self.assertEqual(tmp.link_method, "structured_dictionary")
        self.assertEqual(tmp.link_confidence, "medium")   # same-tier Kelvin rival refuted
        self.assertEqual(tmp.link_evidence, "cb1 row 'tmp'")
        self.assertTrue(tmp.value_label)
        # List fields all non-empty — conflict, corroboration, and alternatives together.
        self.assertTrue(tmp.conflicts)
        self.assertIn("Kelvin", tmp.conflicts[0])
        self.assertEqual(tmp.corroborated_by, ["cb2 row 'tmp'"])
        self.assertEqual(len(tmp.alternatives), 2)        # cb2 and cb3 (tmp is not a coordinate)
        # Nothing in the serialized form is None/empty either.
        d = tmp.to_dict()
        empty = [k for k, v in d.items() if v in (None, "", [], "none")]
        self.assertEqual(empty, [], f"unexpected empty ResolvedColumn fields: {empty}")

    def test_every_link_method_is_exercised(self):
        cat = self._catalog()
        methods = {c.name: c.link_method for c in cat.columns}
        self.assertEqual(methods["la"], "structured_dictionary")
        self.assertEqual(methods["note"], "lexical_prose")
        self.assertEqual(methods["dt"], "value_prior")
        self.assertEqual(methods["oid"], "none")

    def test_every_confidence_level_is_exercised(self):
        cat = self._catalog()
        conf = {c.name: c.link_confidence for c in cat.columns}
        self.assertEqual(conf["la"], "high")     # corroborated
        self.assertEqual(conf["dt"], "high")     # self-evident temporal
        self.assertEqual(conf["tmp"], "medium")  # tie broken by the values
        self.assertEqual(conf["note"], "medium") # lone prose
        self.assertEqual(conf["frac"], "low")    # chosen claim refuted
        self.assertEqual(conf["oid"], "none")    # abstained

    def test_every_value_label_is_exercised(self):
        cat = self._catalog()
        labels = {c.value_label for c in cat.columns}
        self.assertEqual(labels, {"coordinate", "temporal", "categorical", "numeric"})

    def test_chosen_claim_refuted_gives_low_and_a_conflict(self):
        frac = self._catalog().get("frac")
        self.assertEqual(frac.link_confidence, "low")
        self.assertTrue(frac.conflicts)
        self.assertIn("percentage", frac.conflicts[0])
        self.assertEqual(frac.corroborated_by, [])   # nothing agreed with it

    def test_corroboration_lifts_a_clean_agreement_to_high(self):
        la = self._catalog().get("la")
        self.assertEqual(la.link_confidence, "high")
        self.assertEqual(la.corroborated_by, ["cb2 row 'la'"])
        self.assertEqual(la.conflicts, [])           # no disagreement, no refutation

    def test_abstained_column_leaves_the_link_fields_empty(self):
        oid = self._catalog().get("oid")
        self.assertEqual(oid.link_method, "none")
        self.assertEqual(oid.link_confidence, "none")
        self.assertIsNone(oid.description)
        self.assertEqual(oid.value_label, "numeric")  # the coarse prior is still kept


class MultiTableBundleTest(unittest.TestCase):
    """A real repo is many tables; resolve_bundle spans all their columns at once."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Two data tables. Both have opaque column names; a shared codebook that
        # documents columns across *both* resolves them wherever they live.
        pd.DataFrame({"a": ["10.x/y"], "b": ["A survey dataset"]}).to_csv(
            os.path.join(self.dir, "dataset.csv"), index=False
        )
        pd.DataFrame({"a": [96.0], "t": [10.0]}).to_csv(
            os.path.join(self.dir, "measure.csv"), index=False
        )
        pd.DataFrame({
            "variable": ["a", "b", "t"],
            "description": ["Dataset DOI", "Dataset title", "Water temperature"],
        }).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)

        self.dataset = create_context(os.path.join(self.dir, "dataset.csv"), name="dataset")
        self.measure = create_context(os.path.join(self.dir, "measure.csv"), name="measure")
        self.codebook = create_context(os.path.join(self.dir, "codebook.csv"), name="codebook")

    def tearDown(self):
        clear_registry()

    def test_bundle_catalog_spans_every_table(self):
        cat = resolve_bundle([self.dataset, self.measure], sources=[self.codebook])
        by_resource = {}
        for c in cat.columns:
            by_resource.setdefault(c.resource, set()).add(c.name)
        self.assertEqual(by_resource["dataset"], {"a", "b"})
        self.assertEqual(by_resource["measure"], {"a", "t"})

    def test_same_name_in_two_tables_is_disambiguated_by_resource(self):
        # Column 'a' exists in *both* tables, so it appears twice in the catalog.
        cat = resolve_bundle([self.dataset, self.measure], sources=[self.codebook])
        a_cols = [c for c in cat.columns if c.name == "a"]
        self.assertEqual(len(a_cols), 2)  # both kept, not collapsed to one
        # find(resource) returns the right table's column; get() (name-only) can't —
        # it just returns the first, which is why routing must use find().
        self.assertEqual(cat.find("a", "dataset").resource, "dataset")
        self.assertEqual(cat.find("a", "measure").resource, "measure")
        self.assertIsNot(cat.find("a", "dataset"), cat.find("a", "measure"))
        self.assertEqual(cat.get("a").resource, "dataset")  # ambiguous: first only

    def test_routes_fields_to_columns_across_tables(self):
        from typing import Optional

        from pydantic import BaseModel, Field

        from src.router import compile_field_plan, route_fields

        class Meta(BaseModel):
            doi: Optional[str] = Field(default=None, description="the dataset DOI")
            water_temperature: Optional[float] = Field(default=None, description="the water temperature")

        cat = resolve_bundle([self.dataset, self.measure], sources=[self.codebook])
        fp = route_fields(Meta, catalog=cat, docs=[])
        doi = fp.routings["doi"].candidates[0]
        temp = fp.routings["water_temperature"].candidates[0]
        self.assertEqual((doi.resource, doi.locator), ("dataset", "a"))
        self.assertEqual((temp.resource, temp.locator), ("measure", "t"))
        # The compiler groups the two fields into separate per-table tasks.
        plan = compile_field_plan(fp)
        col_tasks = [t for t in plan.steps if t.fields and t.player == "data_analyst"]
        scopes = {tuple(t.target_resources) for t in col_tasks}
        self.assertIn(("dataset",), scopes)
        self.assertIn(("measure",), scopes)


class ProseReaderTierTest(unittest.TestCase):
    """The retrieve-then-read tier: localize a chunk, then read a *cued* definition.

    It is opt-in (``prose_reader=``) and registered at the same tier as the glossary
    regex, so :func:`_decide` treats the two prose methods as corroboration or
    conflict with no special-casing. These tests exercise: the deterministic reader's
    extraction, retrieval localizing the right chunk across long/many documents, the
    opt-in gate, and same-tier corroboration/conflict through the decision step.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # mass/epoc are generic float names: without a described source they abstain
        # (numeric long tail), so any resolution here comes from the prose reader.
        pd.DataFrame({"mass": [12.1, 15.3, 9.8], "epoc": [1.0, 2.0, 3.0]}).to_csv(
            os.path.join(self.dir, "fish.csv"), index=False
        )
        self.tab = create_context(os.path.join(self.dir, "fish.csv"), name="fish")
        self.reader = DeterministicProseReader()

    def tearDown(self):
        clear_registry()

    def _doc(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(text)
        return create_context(path, name=name.split(".")[0])

    # --- the deterministic reader in isolation ---------------------------

    def test_deterministic_reader_extracts_forward_reversed_and_abstains(self):
        r = DeterministicProseReader()
        fwd = r.read(column="mass", dtype="float", chunk="pH – acclimation pH; Mass – fish mass (g)")
        self.assertEqual((fwd.description, fwd.units), ("fish mass", "g"))  # forward + unit split
        rev = r.read(column="mass", dtype="float", chunk="Body mass (mass) recorded in grams.")
        self.assertEqual(rev.description, "Body mass")                      # reversed, phrase before token
        # A sentence that merely mentions the token, with no definitional cue, abstains.
        self.assertIsNone(r.read(column="mass", dtype="float", chunk="Mass matters for fish."))

    def test_llm_reader_is_a_documented_seam(self):
        reader = LLMProseReader(client=object())
        self.assertTrue(hasattr(reader, "read"))
        with self.assertRaises(NotImplementedError):
            reader.read(column="mass", dtype="float", chunk="anything")

    # --- the tier in the resolution pipeline -----------------------------

    def test_reader_resolves_a_reversed_definition_the_regex_misses(self):
        # The token is defined in parens *after* the phrase ("body mass (mass)"), a
        # shape the whole-doc glossary regex cannot match — only a reader can.
        doc = self._doc(
            "manuscript.md",
            "# Methods\n\n"
            "Fish were held at 15C for two weeks prior to trials. "
            "Body mass (mass) was recorded to the nearest 0.1 g before each swim test. "
            "We then measured excess post-exercise oxygen consumption (EPOC).\n",
        )
        # Opt-in gate: with no reader the reversed definition is invisible → abstains.
        without = resolve_catalog(self.tab, sources=[doc]).get("mass")
        self.assertEqual(without.link_method, "none")
        # With the reader it is localized and read.
        with_reader = resolve_catalog(
            self.tab, sources=[doc], prose_reader=self.reader
        ).get("mass")
        self.assertEqual(with_reader.link_method, "prose_read")
        self.assertIn("mass", with_reader.description.lower())
        self.assertEqual(with_reader.link_confidence, "medium")
        self.assertIn("manuscript", with_reader.link_evidence)   # cites the chunk's offset

    def test_retrieval_localizes_the_defining_document_among_many(self):
        # A long decoy that mentions the token constantly but never defines it, and a
        # short appendix that defines it in a reversed (regex-invisible) shape. Only a
        # reader can resolve it, and retrieval must rank the appendix chunk to the top.
        decoy = self._doc("intro.md", "# Introduction\n\n" + ("Mass matters for fish physiology. " * 40))
        appendix = self._doc(
            "appendix.md",
            "# Appendix\n\nWet body mass (mass) was measured at the start of each trial.\n",
        )
        col = resolve_catalog(
            self.tab, sources=[decoy, appendix], prose_reader=self.reader
        ).get("mass")
        self.assertEqual(col.link_method, "prose_read")
        self.assertEqual(col.description, "Wet body mass")
        self.assertIn("appendix", col.link_evidence)   # the definer, not the decoy

    def test_reader_abstains_when_no_document_defines_the_column(self):
        doc = self._doc("unrelated.md", "# Notes\n\nThe experiment ran for six weeks in spring.\n")
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=self.reader).get("mass")
        self.assertEqual(col.link_method, "none")   # nothing to retrieve/read → honest abstention

    def test_glossary_and_reader_corroborate_at_the_same_tier(self):
        # One doc whose line both the regex and the reader read identically ("wet body
        # mass"): same tier, same claim → corroboration lifts confidence to high.
        doc = self._doc("readme.md", "# Vars\n\nmass – wet body mass; epoc – oxygen debt\n")
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=self.reader).get("mass")
        self.assertEqual(col.link_method, "lexical_prose")   # regex added first, wins source-order
        self.assertEqual(col.description, "wet body mass")
        self.assertEqual(col.link_confidence, "high")        # corroborated by the reader
        self.assertEqual(len(col.corroborated_by), 1)
        self.assertEqual(col.conflicts, [])

    def test_glossary_and_reader_conflict_is_surfaced(self):
        # Two tier-2 prose claims that disagree: the glossary wins source-order but the
        # reader's differing read is recorded as a conflict and kept as an alternative.
        g = self._doc("glossary.md", "# G\n\nmass – wet body mass\n")
        m = self._doc("manuscript.md", "# M\n\nFish total length (mass) was measured in cm.\n")
        col = resolve_catalog(self.tab, sources=[g, m], prose_reader=self.reader).get("mass")
        self.assertEqual(col.description, "wet body mass")
        self.assertEqual(col.link_confidence, "medium")      # contested, unadjudicated
        self.assertTrue(col.conflicts)
        self.assertTrue(any(a["method"] == "prose_read" for a in col.alternatives))

    # --- batched, cached call shape (cost control for an expensive reader) ---

    def test_reader_is_called_once_per_chunk_not_per_column(self):
        # One chunk defines *both* columns; both retrieve it, so a chunk-major reader
        # sees that chunk a single time, covering both columns in one call — the shape
        # that makes an LLM backend cost one round-trip per chunk, not per column.
        doc = self._doc("glossary.md", "# Vars\n\nmass – wet body mass; epoc – oxygen debt\n")
        spy = _CountingReader()
        cat = resolve_catalog(self.tab, sources=[doc], prose_reader=spy)
        self.assertEqual(len(spy.calls), 1)                  # one chunk → one call
        self.assertEqual(set(spy.calls[0][0]), {"mass", "epoc"})   # both columns batched
        self.assertEqual(cat.get("mass").description, "wet body mass")
        self.assertEqual(cat.get("epoc").description, "oxygen debt")

    def test_cached_reader_reads_each_chunk_once_including_negatives(self):
        spy = _CountingReader()
        cached = CachedProseReader(spy)
        chunk = "mass – wet body mass"
        # 'mass' resolves; 'foo' abstains — the cache must remember *both*.
        first = cached.read_many(columns=[("mass", "float"), ("foo", "float")], chunk=chunk)
        self.assertEqual(first["mass"].description, "wet body mass")
        self.assertNotIn("foo", first)
        self.assertEqual(len(spy.calls), 1)
        # An identical second call is served entirely from cache — including the
        # negative for 'foo', so no fresh inner read happens.
        second = cached.read_many(columns=[("mass", "float"), ("foo", "float")], chunk=chunk)
        self.assertEqual(len(spy.calls), 1)                  # inner not called again
        self.assertEqual(second["mass"].description, "wet body mass")

    def test_cache_is_shared_across_tables_of_a_bundle(self):
        # The same reader instance is threaded through every table's resolution, so a
        # chunk shared by two tables' columns is read once for the whole bundle.
        pd.DataFrame({"mass": [1.0, 2.0]}).to_csv(os.path.join(self.dir, "t1.csv"), index=False)
        pd.DataFrame({"mass": [3.0, 4.0]}).to_csv(os.path.join(self.dir, "t2.csv"), index=False)
        t1 = create_context(os.path.join(self.dir, "t1.csv"), name="t1")
        t2 = create_context(os.path.join(self.dir, "t2.csv"), name="t2")
        doc = self._doc("m.md", "# M\n\nWet body mass (mass) was measured in grams.\n")
        spy = _CountingReader()
        cached = CachedProseReader(spy)
        cat = resolve_bundle([t1, t2], sources=[doc], prose_reader=cached)
        # Both tables' 'mass' resolve, but the shared chunk is read from source once.
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(cat.find("mass", "t1").description, "Wet body mass")
        self.assertEqual(cat.find("mass", "t2").description, "Wet body mass")


if __name__ == "__main__":
    unittest.main()
