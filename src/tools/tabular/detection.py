"""
Shared column-detection heuristics for the spatial and temporal toolsets.

Kept separate from the tools themselves: these are ordinary functions, unit
testable without a context or a registry.
"""

import ast
import re
from typing import Any, Optional, Tuple

import pandas as pd

TEMPORAL_PATTERNS = [
    r"date", r"time", r"timestamp", r"datetime", r"created", r"updated",
    r"modified", r"start", r"end", r"begin", r"expire", r"valid", r"year",
    r"month", r"day", r"hour", r"minute", r"second", r"period", r"duration",
    r"_at$", r"_on$", r"_dt$",
]

SPATIAL_PATTERNS = [
    r"lat(?:itude)?", r"lon(?:g(?:itude)?)?", r"coord", r"geo", r"location",
    r"position", r"point", r"polygon", r"geometry", r"geom", r"wkt", r"wkb",
    r"x_?coord", r"y_?coord", r"easting", r"northing", r"spatial", r"place",
    r"address", r"city", r"state", r"country", r"zip", r"postal", r"region",
    r"bbox", r"bounds", r"extent",
]

WKT_TYPES = [
    "POINT", "LINESTRING", "POLYGON",
    "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION",
]

_TUPLE_PAIR_RE = re.compile(
    r"^\(\s*([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*,\s*"
    r"([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*\)$"
)


def is_temporal_column_name(column_name: str) -> bool:
    """Check if a column name suggests temporal data."""
    name_lower = column_name.lower()
    return any(re.search(p, name_lower) for p in TEMPORAL_PATTERNS)


def is_spatial_column_name(column_name: str) -> bool:
    """Check if a column name suggests spatial data."""
    name_lower = column_name.lower()
    return any(re.search(p, name_lower) for p in SPATIAL_PATTERNS)


def detect_temporal_dtype(series: pd.Series) -> Optional[str]:
    """Detect temporal data from dtype, or from parseable date strings."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime64"

    if pd.api.types.is_timedelta64_dtype(series):
        return "timedelta64"

    if series.dtype == object or str(series.dtype) == "string":
        sample = series.dropna().head(100)
        if len(sample) == 0:
            return None
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            if parsed.notna().sum() / len(sample) > 0.8:
                return "datetime_string"
        except Exception:
            pass

    return None


def detect_coordinate_values(series: pd.Series) -> Optional[str]:
    """Detect if numeric values fall within coordinate ranges."""
    if not pd.api.types.is_numeric_dtype(series):
        return None

    sample = series.dropna()
    if len(sample) == 0:
        return None

    min_val, max_val = sample.min(), sample.max()

    if -90 <= min_val and max_val <= 90:
        return "possible_latitude"
    if -180 <= min_val and max_val <= 180:
        return "possible_longitude"
    return None


def detect_wkt_geometry(series: pd.Series) -> Optional[str]:
    """Detect if string values contain WKT geometry."""
    if series.dtype != object and str(series.dtype) != "string":
        return None

    sample = series.dropna().head(50)
    if len(sample) == 0:
        return None

    wkt_count = sum(
        1
        for val in sample
        if any(
            re.match(rf"^{gtype}\s*\(", str(val).upper().strip())
            for gtype in WKT_TYPES
        )
    )
    return "wkt_geometry" if wkt_count / len(sample) > 0.5 else None


def parse_two_float_tuple(val: Any) -> Optional[Tuple[float, float]]:
    """Parse values like '(-7.82, 54.26)' into two floats."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None

    s = str(val).strip()
    match = _TUPLE_PAIR_RE.match(s)
    if match:
        return float(match.group(1)), float(match.group(2))

    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, (list, tuple)) and len(parsed) == 2:
            return float(parsed[0]), float(parsed[1])
    except (ValueError, SyntaxError, TypeError):
        pass
    return None


def sample_tuple_parse_rate(
    series: pd.Series, max_sample: int = 80
) -> Tuple[int, int]:
    """Return (parsed_count, tried_count) over a non-null head sample."""
    sample = series.dropna().head(max_sample)
    tried = len(sample)
    if tried == 0:
        return 0, 0
    parsed = sum(1 for v in sample if parse_two_float_tuple(v) is not None)
    return parsed, tried
