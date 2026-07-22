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


if __name__ == "__main__":
    unittest.main()
