"""Tests for the thresholds registry (src/thresholds.py)."""

import os
import sys
import unittest
from dataclasses import fields, replace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import thresholds
from src.thresholds import Thresholds, env_name


class ThresholdsTest(unittest.TestCase):
    def test_every_threshold_is_documented_for_the_panel(self):
        for spec in fields(Thresholds):
            self.assertTrue(spec.metadata["label"], spec.name)
            self.assertTrue(spec.metadata["help"], spec.name)
            self.assertIn(spec.metadata["stage"],
                          {thresholds.CATALOG, thresholds.ROUTER, thresholds.COMPILER})

    def test_the_environment_overrides_a_default(self):
        with patch.dict(os.environ, {env_name("router_read_all_max_passages"): "3"}):
            self.assertEqual(thresholds.current().router_read_all_max_passages, 3)

    def test_a_malformed_environment_value_names_its_variable(self):
        with patch.dict(os.environ, {env_name("catalog_profile_sample"): "lots"}):
            with self.assertRaises(ValueError) as caught:
                thresholds.current()
        self.assertIn("THRESHOLD_CATALOG_PROFILE_SAMPLE", str(caught.exception))

    def test_an_override_beats_the_environment_and_ends_with_its_block(self):
        with patch.dict(os.environ, {env_name("router_max_fields_per_call"): "7"}):
            with thresholds.use(replace(Thresholds(), router_max_fields_per_call=2)):
                self.assertEqual(thresholds.current().router_max_fields_per_call, 2)
            self.assertEqual(thresholds.current().router_max_fields_per_call, 7)

    def test_the_environment_round_trips(self):
        values = replace(Thresholds(), catalog_grounding_support=0.75)
        with patch.dict(os.environ, values.environment()):
            self.assertEqual(Thresholds.from_environment(), values)


if __name__ == "__main__":
    unittest.main()
