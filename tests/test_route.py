"""Tests for the field router — buckets, coverage, two-hop assurance (M3, layer 4)."""

import os
import sys
import tempfile
import unittest
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.router import resolve_catalog, route_fields
from src.tools.base import clear_registry


class Meta(BaseModel):
    title: str = Field(description="The title of the dataset")
    record_count: Optional[int] = Field(default=None, description="The number of records in the data")
    lat_field: Optional[float] = Field(default=None, description="the latitude coordinate column")
    mystery: Optional[str] = Field(default=None, description="qzzx wvvq blorp")


class RouterTest(unittest.TestCase):
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

    def _plan(self, with_codebook=True):
        sources = [self.cb] if with_codebook else []
        catalog = resolve_catalog(self.tab, sources=sources)
        return route_fields(Meta, catalog=catalog, docs=[self.doc])

    def test_structural_field_binds_to_a_tool(self):
        r = self._plan().routings["record_count"]
        self.assertEqual(r.bucket, "structural")
        self.assertEqual(r.assurance, "high")
        self.assertEqual(r.candidates[0].locator, "get_item_count")

    def test_structural_binding_is_standard_agnostic(self):
        """A differently-worded count field still binds — no keyword table.

        The bucket comes from ranking the field's query against the tool's own
        description, so wording the fixture never anticipated ("the row count of
        the table") routes structurally all the same.
        """

        class Other(BaseModel):
            n_rows: Optional[int] = Field(default=None, description="The row count of the table")

        catalog = resolve_catalog(self.tab, sources=[self.cb])
        r = route_fields(Other, catalog=catalog, docs=[self.doc]).routings["n_rows"]
        self.assertEqual(r.bucket, "structural")
        self.assertEqual(r.candidates[0].locator, "get_item_count")

    def test_ambiguous_structural_routes_to_a_column(self):
        r = self._plan().routings["lat_field"]
        self.assertEqual(r.bucket, "ambiguous_structural")
        self.assertEqual(r.candidates[0].locator, "la")

    def test_narrative_routes_to_a_document_span(self):
        r = self._plan().routings["title"]
        self.assertEqual(r.bucket, "narrative")
        self.assertTrue(r.candidates)
        self.assertEqual(r.candidates[0].kind, "quoted_span")

    def test_unresolved_field_is_flagged(self):
        plan = self._plan()
        r = plan.routings["mystery"]
        self.assertEqual(r.bucket, "unresolved")
        self.assertEqual(r.status, "unresolved")
        self.assertEqual(r.assurance, "none")
        self.assertIn("mystery", plan.unresolved())

    def test_assurance_inherits_the_catalog_confidence(self):
        """Two-hop: a column resolved high → high; resolved by value only → medium."""
        self.assertEqual(self._plan(with_codebook=True).routings["lat_field"].assurance, "high")
        self.assertEqual(self._plan(with_codebook=False).routings["lat_field"].assurance, "medium")

    def test_coverage_report(self):
        cov = self._plan().coverage()
        self.assertEqual(cov["total"], 4)
        self.assertEqual(cov["routed"], 3)
        self.assertEqual(cov["unresolved"], ["mystery"])
        self.assertEqual(cov["by_bucket"]["structural"], 1)

    def test_plan_is_serializable(self):
        d = self._plan().to_dict()
        self.assertEqual(d["schema_name"], "Meta")
        self.assertIn("coverage", d)
        self.assertIn("record_count", d["routings"])


if __name__ == "__main__":
    unittest.main()
