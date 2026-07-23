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


class SurveyMeta(BaseModel):
    title: str = Field(description="The title of the dataset")
    abstract: Optional[str] = Field(default=None, description="An overview of the survey and what it records")
    methods: Optional[str] = Field(default=None, description="How the sampling was carried out")
    license: Optional[str] = Field(default=None, description="The licence under which the data is released")
    min_latitude: Optional[float] = Field(default=None, description="The southernmost latitude sampled")
    max_longitude: Optional[float] = Field(default=None, description="The easternmost longitude sampled")
    sampling_dates: Optional[str] = Field(default=None, description="The date range over which sampling occurred")
    species_variable: Optional[str] = Field(default=None, description="The recorded species taxon")
    individual_count: Optional[str] = Field(default=None, description="The number of individuals recorded per observation")
    water_temperature: Optional[str] = Field(default=None, description="The water temperature measured at each station")
    row_count: Optional[int] = Field(default=None, description="The row count of the observations table")
    provenance_notes: Optional[str] = Field(default=None, description="zzzq wxyv unmatchable")


class RouterRealisticTest(unittest.TestCase):
    """A multi-column bundle: routing must pick the right column among competitors."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({
            "station_id": list(range(1, 9)),
            "lat": [50.1, 50.5, 51.0, 51.4, 52.0, 52.6, 53.1, 53.8],
            "lon": [-9.5, -9.1, -8.7, -8.2, -7.8, -7.3, -6.9, -6.4],
            "obs_date": ["2021-05-0%d" % d for d in range(1, 9)],
            "taxon": ["ABC", "DEF", "GHI", "ABC", "DEF", "GHI", "ABC", "DEF"],
            "abundance": [3, 7, 12, 5, 9, 2, 14, 6],
            "water_temp": [11.2, 12.5, 10.8, 13.1, 12.0, 11.7, 13.4, 12.9],
        }).to_csv(os.path.join(self.dir, "observations.csv"), index=False)
        pd.DataFrame({
            "variable": ["station_id", "lat", "lon", "obs_date", "taxon", "abundance", "water_temp"],
            "description": [
                "Monitoring station identifier",
                "Latitude in decimal degrees",
                "Longitude in decimal degrees",
                "Date of the observation",
                "Taxon code for the recorded species",
                "Number of individuals observed",
                "Water temperature at the station",
            ],
            "units": ["", "degrees", "degrees", "", "", "count", "degrees Celsius"],
        }).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)
        with open(os.path.join(self.dir, "README.md"), "w") as f:
            f.write(
                "# Coastal Rockpool Survey 2021\n\n"
                "## Overview\n\nThe Coastal Rockpool Survey dataset records species "
                "abundance across intertidal stations.\n\n"
                "## Methods\n\nSampling was carried out by timed searches at each "
                "station during low tide.\n\n"
                "## Access\n\nReleased under the Open Data Commons Attribution licence.\n"
            )
        self.tab = create_context(os.path.join(self.dir, "observations.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "codebook.csv"), name="cb")
        self.readme = create_context(os.path.join(self.dir, "README.md"), name="readme")

    def tearDown(self):
        clear_registry()

    def _plan(self):
        catalog = resolve_catalog(self.tab, sources=[self.cb, self.readme])
        return route_fields(SurveyMeta, catalog=catalog, docs=[self.readme])

    def test_disambiguates_latitude_from_longitude(self):
        r = self._plan().routings
        self.assertEqual(r["min_latitude"].candidates[0].locator, "lat")
        self.assertEqual(r["max_longitude"].candidates[0].locator, "lon")

    def test_routes_each_measurement_to_its_own_column(self):
        r = self._plan().routings
        self.assertEqual(r["sampling_dates"].candidates[0].locator, "obs_date")
        self.assertEqual(r["species_variable"].candidates[0].locator, "taxon")
        self.assertEqual(r["individual_count"].candidates[0].locator, "abundance")

    def test_column_wins_over_a_same_word_competitor(self):
        """`water_temperature` mentions "station" but must resolve to water_temp."""
        self.assertEqual(self._plan().routings["water_temperature"].candidates[0].locator, "water_temp")

    def test_buckets_across_the_schema(self):
        r = self._plan().routings
        self.assertEqual(r["row_count"].bucket, "structural")
        self.assertEqual(r["title"].bucket, "narrative")
        self.assertEqual(r["provenance_notes"].bucket, "unresolved")

    def test_codebook_resolved_columns_are_high_assurance(self):
        r = self._plan().routings
        for f in ("min_latitude", "sampling_dates", "water_temperature"):
            self.assertEqual(r[f].assurance, "high")

    def test_coverage_over_a_full_schema(self):
        cov = self._plan().coverage()
        self.assertEqual(cov["total"], 12)
        self.assertEqual(cov["unresolved"], ["provenance_notes"])
        self.assertEqual(cov["routed"], 11)


if __name__ == "__main__":
    unittest.main()
