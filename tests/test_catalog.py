"""Tests for catalog resolution — symbol linking, priors, cross-check (M2, layer 3)."""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.router import resolve_catalog
from src.tools.base import clear_registry


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


if __name__ == "__main__":
    unittest.main()
