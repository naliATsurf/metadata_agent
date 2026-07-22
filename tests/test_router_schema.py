"""Tests for the field-router schema walker (layer 1)."""

import os
import sys
import unittest
from typing import Dict, Optional

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.router import FieldSpec, walk_schema
from src.standards import get_schema_for_standard


class Inner(BaseModel):
    a: str = Field(description="inner a")
    b: Optional[int] = Field(default=None, description="inner b")


class Outer(BaseModel):
    title: str = Field(description="the title")
    nested: Inner = Field(description="a nested model")
    box: Optional[Dict[str, float]] = Field(default=None, description="a bbox")


class SchemaWalkerTest(unittest.TestCase):
    def _by_path(self, specs):
        return {s.path: s for s in specs}

    def test_nested_model_flattens_to_dotted_paths(self):
        paths = {s.path for s in walk_schema(Outer)}
        self.assertEqual(paths, {"title", "nested.a", "nested.b", "box"})

    def test_container_is_a_leaf_not_recursed(self):
        spec = self._by_path(walk_schema(Outer))["box"]
        self.assertTrue(spec.type.startswith("Optional["))
        self.assertEqual(spec.description, "a bbox")

    def test_required_flag_tracks_optionality(self):
        specs = self._by_path(walk_schema(Outer))
        self.assertTrue(specs["title"].required)       # required scalar
        self.assertTrue(specs["nested.a"].required)    # required inside the model
        self.assertFalse(specs["nested.b"].required)   # Optional inside the model
        self.assertFalse(specs["box"].required)        # Optional container

    def test_description_is_carried_as_the_query(self):
        specs = self._by_path(walk_schema(Outer))
        self.assertEqual(specs["title"].description, "the title")

    def test_specs_are_frozen_fieldspecs(self):
        spec = walk_schema(Outer)[0]
        self.assertIsInstance(spec, FieldSpec)
        with self.assertRaises(Exception):
            spec.path = "mutated"  # frozen dataclass

    def test_walks_a_real_standard(self):
        specs = self._by_path(walk_schema(get_schema_for_standard("field_router_test")))
        self.assertEqual(len(specs), 11)
        self.assertTrue(specs["title"].required)
        self.assertFalse(specs["record_count"].required)
        # spatial_coverage is Optional[Dict] — a leaf, not recursed into min_lat/...
        self.assertIn("spatial_coverage", specs)
        self.assertNotIn("spatial_coverage.min_lat", specs)


if __name__ == "__main__":
    unittest.main()
