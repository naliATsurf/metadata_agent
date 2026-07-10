"""Tests for tuple-coordinate spatial tools (e.g. S2BMS tuple_coords)."""
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context.context_factory import create_context
from src.tools.base import register_context
from src.tools.tabular import spatial


class TestSpatialTupleTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._csv = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "data",
                "S2BMS",
                "bms_presence_y-2018-2019_th-200.csv",
            )
        )

    def test_tuple_column_detection_and_extent(self):
        if not os.path.isfile(self._csv):
            self.skipTest(f"S2BMS fixture missing: {self._csv}")

        ctx = create_context({"event": self._csv}, name="s2bms_tuple_test")
        key = register_context(f"ctx_test_spatial_tuple_{uuid.uuid4().hex[:8]}", ctx)

        det = spatial.detect_spatial_columns.invoke(
            {"context_key": key, "resource": "event"}
        )
        self.assertNotIn("error", det)
        tcc = det.get("tuple_coord_columns") or []
        self.assertTrue(any(c.get("column") == "tuple_coords" for c in tcc))

        ext = spatial.get_spatial_extent_from_tuple_column.invoke(
            {
                "context_key": key,
                "resource": "event",
                "column": "tuple_coords",
                "tuple_order": "lon_lat",
            }
        )
        self.assertNotIn("error", ext)
        bb = ext["bounding_box"]
        self.assertAlmostEqual(bb["min_lat"], 49.969258, places=5)
        self.assertAlmostEqual(bb["max_lat"], 58.606506, places=5)
        self.assertAlmostEqual(bb["min_lon"], -7.824283, places=5)
        self.assertAlmostEqual(bb["max_lon"], 1.693422, places=5)
        self.assertEqual(ext.get("valid_point_count"), 1455)


if __name__ == "__main__":
    unittest.main()
