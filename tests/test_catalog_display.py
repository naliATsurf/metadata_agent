"""Tests for the catalog display layer (src/router/display.py)."""

import os
import sys
import tempfile
import unittest

import pandas as pd
from rich.console import Console
from rich.table import Table

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.router import (
    catalog_conflicts,
    catalog_overview,
    catalog_summary,
    render_catalog,
    resolve_catalog,
)
from src.router.catalog import Catalog
from src.tools.base import clear_registry


def _plain(catalog) -> str:
    """Render to a width-fixed, recording Console and return the plain text."""
    console = Console(record=True, width=200)
    render_catalog(catalog, console)
    return console.export_text()


class CatalogDisplayTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame(
            {"la": [53.1, 53.2, 53.3], "tmp": [4.5, 10.2, 21.0], "oid": [1, 2, 3]}
        ).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        # A codebook that resolves la/tmp; tmp's Kelvin claim is refuted by the values.
        pd.DataFrame({
            "variable": ["la", "tmp"],
            "label": ["Latitude of the point", "Air temperature"],
            "units": ["decimal degrees", "Kelvin"],
        }).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.cb = create_context(os.path.join(self.dir, "codebook.csv"), name="codebook")
        self.catalog = resolve_catalog(self.tab, sources=[self.cb])

    def tearDown(self):
        clear_registry()

    def test_overview_is_a_table_with_a_row_per_column(self):
        table = catalog_overview(self.catalog)
        self.assertIsInstance(table, Table)
        self.assertEqual(table.row_count, len(self.catalog.columns))

    def test_render_shows_meanings_citations_and_summary(self):
        out = _plain(self.catalog)
        self.assertIn("Latitude of the point", out)   # resolved meaning
        self.assertIn("codebook row", out)             # its citation
        self.assertIn("columns resolved", out)         # the summary line

    def test_conflicts_section_surfaces_the_refuted_claim(self):
        lines = catalog_conflicts(self.catalog)
        self.assertIsNotNone(lines)                    # tmp's Kelvin conflict makes it interesting
        self.assertTrue(any("Kelvin" in line for line in lines))
        self.assertIn("conflict", _plain(self.catalog).lower())

    def test_summary_counts_resolved_columns(self):
        text = catalog_summary(self.catalog).plain
        # la + tmp resolved from the codebook; oid (an int id) abstains.
        self.assertIn("2/3 columns resolved", text)

    def test_clean_catalog_has_no_conflicts_section(self):
        # A single categorical column resolved by one codebook: no value-prior
        # alternative, no rival source → nothing contested, corroborated, or shelved.
        pd.DataFrame({"site": ["AA", "BB", "AA"]}).to_csv(
            os.path.join(self.dir, "only.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "only.csv"), name="only")
        pd.DataFrame({"variable": ["site"], "label": ["Site name"]}).to_csv(
            os.path.join(self.dir, "cbsite.csv"), index=False
        )
        cbsite = create_context(os.path.join(self.dir, "cbsite.csv"), name="cbsite")
        clean = resolve_catalog(tab, sources=[cbsite])
        self.assertEqual(clean.get("site").link_method, "structured_dictionary")
        self.assertIsNone(catalog_conflicts(clean))

    def test_empty_catalog_renders_without_crashing(self):
        out = _plain(Catalog(resource="none", columns=[]))
        self.assertIn("0/0 columns resolved", out)


if __name__ == "__main__":
    unittest.main()
