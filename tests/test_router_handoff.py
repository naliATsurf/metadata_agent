"""The catalog is resolved once and routed separately — in memory or from a file."""

import argparse
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console

from examples import field_router_plan, resolve_catalog
from src.router import Catalog, ResolvedBundle
from src.tools.base import clear_registry

BUNDLE = Path(__file__).resolve().parents[1] / "data/tests/router_test"


def _quiet() -> Console:
    return Console(file=io.StringIO())


def _resolve() -> ResolvedBundle:
    args = resolve_catalog.build_parser().parse_args(["--bundle", str(BUNDLE)])
    return resolve_catalog.run(args, _quiet())


def _router_args(catalog: Path) -> argparse.Namespace:
    return field_router_plan.build_parser().parse_args(
        ["--catalog", str(catalog), "--standard", "field_router_test"]
    )


class ResolutionRoundTripTest(unittest.TestCase):
    def tearDown(self):
        clear_registry()

    def test_catalog_round_trips_through_its_dict(self):
        catalog = _resolve().catalog
        self.assertEqual(Catalog.from_dict(catalog.to_dict()), catalog)

    def test_resolution_saves_and_loads_whole(self):
        resolved = _resolve()
        path = Path(tempfile.mkdtemp()) / "catalog.json"
        self.assertEqual(ResolvedBundle.load(resolved.save(path)), resolved)

    def test_resolution_records_what_it_used(self):
        args = resolve_catalog.build_parser().parse_args(
            ["--bundle", str(BUNDLE), "--doc", "none"]
        )
        resolved = resolve_catalog.run(args, _quiet())
        self.assertEqual(resolved.root, BUNDLE)
        self.assertEqual(resolved.documents, [])            # the subset, not the bundle
        self.assertEqual([p.name for p in resolved.codebooks], ["codebook.csv"])
        self.assertEqual(resolved.reader, "off")


class RouterConsumesResolutionTest(unittest.TestCase):
    def tearDown(self):
        clear_registry()

    def test_routing_from_a_file_matches_routing_in_memory(self):
        resolved = _resolve()
        path = resolved.save(Path(tempfile.mkdtemp()) / "catalog.json")
        from_file = field_router_plan.run(_router_args(path), _quiet())
        in_memory = field_router_plan.run(
            _router_args(Path("unused.json")), _quiet(), resolved=resolved
        )
        self.assertEqual(from_file.field_plan.to_dict(), in_memory.field_plan.to_dict())
        self.assertIs(in_memory.resolved, resolved)          # handed on, not re-resolved

    def test_router_has_no_resolution_arguments(self):
        dests = {a.dest for a in field_router_plan.build_parser()._actions}
        self.assertTrue({"bundle", "dictionary", "doc", "llm_reader"}.isdisjoint(dests))

    def test_a_missing_resolution_says_how_to_make_one(self):
        with self.assertRaises(SystemExit) as caught:
            field_router_plan.run(_router_args(Path("/nonexistent/catalog.json")), _quiet())
        self.assertIn("resolve_catalog.py --out", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
