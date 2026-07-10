"""Spatial analysis tools for row/column contexts."""

import re
from typing import Any, Dict, List

import pandas as pd

from src.context.base_context import TabularContext
from src.tools.base import context_tool
from src.tools.tabular.detection import (
    WKT_TYPES,
    detect_coordinate_values,
    detect_wkt_geometry,
    is_spatial_column_name,
    parse_two_float_tuple,
    sample_tuple_parse_rate,
)

TUPLE_ORDERS = ("lon_lat", "lat_lon")


def _bounds_warnings(min_lat, max_lat, min_lon, max_lon) -> List[str]:
    warnings = []
    if min_lat < -90 or max_lat > 90:
        warnings.append("Latitude values outside valid range [-90, 90]")
    if min_lon < -180 or max_lon > 180:
        warnings.append("Longitude values outside valid range [-180, 180]")
    return warnings


@context_tool(toolset="tabular.spatial", requires=TabularContext)
def detect_spatial_columns(ctx: TabularContext, resource: str = "") -> Dict[str, Any]:
    """Detect columns containing spatial (geographic/coordinate) data in a resource.

    Reports detected coordinate pairs, and `tuple_coord_columns` for string
    columns holding "(a, b)" float pairs. Use the reported column names with
    get_spatial_extent or get_spatial_extent_from_tuple_column.
    """
    resource = resource or ctx.resources[0]
    df = ctx.read_resource(resource)

    spatial_columns: Dict[str, Any] = {}
    tuple_coord_columns: List[Dict[str, Any]] = []

    for col in df.columns:
        series = df[col]
        detected_type = detect_wkt_geometry(series) or detect_coordinate_values(series)

        # Tuple string pairs "(lon, lat)" — common in ecology / BMS exports.
        if detected_type is None and (
            series.dtype == object or str(series.dtype) == "string"
        ):
            parsed, tried = sample_tuple_parse_rate(series)
            if tried > 0 and parsed / tried >= 0.75:
                detected_type = "two_float_tuple_string"
                tuple_coord_columns.append(
                    {
                        "column": col,
                        "tuple_order": "lon_lat",
                        "sample_parse_rate": round(parsed / tried, 3),
                        "note": "Default tuple_order is lon_lat; pass tuple_order='lat_lon' "
                        "to get_spatial_extent_from_tuple_column if your file uses (lat, lon).",
                    }
                )

        name_suggests = is_spatial_column_name(col)
        if name_suggests or detected_type:
            spatial_columns[col] = {
                "name_suggests_spatial": name_suggests,
                "detected_type": detected_type,
                "sample_values": [str(v) for v in series.dropna().head(5)],
            }

    lat_cols = [
        c
        for c, info in spatial_columns.items()
        if info.get("detected_type") == "possible_latitude" or re.search(r"lat", c.lower())
    ]
    lon_cols = [
        c
        for c, info in spatial_columns.items()
        if info.get("detected_type") == "possible_longitude" or re.search(r"lon", c.lower())
    ]
    coordinate_pairs = (
        [{"latitude": lat_cols[0], "longitude": lon_cols[0]}]
        if lat_cols and lon_cols
        else []
    )

    return {
        "resource": resource,
        "spatial_column_count": len(spatial_columns),
        "spatial_columns": spatial_columns,
        "detected_coordinate_pairs": coordinate_pairs,
        "tuple_coord_columns": tuple_coord_columns,
    }


@context_tool(toolset="tabular.spatial", requires=TabularContext)
def analyze_spatial_column(
    ctx: TabularContext, resource: str, column: str
) -> Dict[str, Any]:
    """Analyze one spatial column in detail.

    Returns coordinate ranges for numeric columns, or geometry types for WKT.
    """
    df = ctx.read_resource(resource)
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found in resource '{resource}'")

    series = df[column]
    result: Dict[str, Any] = {
        "column": column,
        "resource": resource,
        "total_values": len(series),
        "null_count": int(series.isnull().sum()),
        "non_null_count": int(series.notna().sum()),
        "dtype": str(series.dtype),
        "sample_values": [str(v) for v in series.dropna().head(5)],
    }

    if pd.api.types.is_numeric_dtype(series):
        valid = series.dropna()
        if len(valid) > 0:
            result["value_range"] = {
                "min": float(valid.min()),
                "max": float(valid.max()),
                "mean": float(valid.mean()),
            }
            if -90 <= valid.min() and valid.max() <= 90:
                result["coordinate_type_hint"] = "latitude"
            elif -180 <= valid.min() and valid.max() <= 180:
                result["coordinate_type_hint"] = "longitude"
            else:
                result["coordinate_type_hint"] = "projected_or_other"

    elif detect_wkt_geometry(series):
        result["geometry_format"] = "WKT"
        geometry_types: Dict[str, int] = {}
        for val in series.dropna().head(100):
            val_upper = str(val).upper().strip()
            for gtype in WKT_TYPES:
                if val_upper.startswith(gtype):
                    geometry_types[gtype] = geometry_types.get(gtype, 0) + 1
                    break
        result["geometry_types"] = geometry_types

    return result


@context_tool(toolset="tabular.spatial", requires=TabularContext)
def get_spatial_extent(
    ctx: TabularContext, resource: str, lat_column: str, lon_column: str
) -> Dict[str, Any]:
    """Calculate the bounding box from separate latitude and longitude columns."""
    df = ctx.read_resource(resource)
    for col in (lat_column, lon_column):
        if col not in df.columns:
            raise ValueError(f"Column '{col}' not found in resource '{resource}'")

    lat = pd.to_numeric(df[lat_column], errors="coerce").dropna()
    lon = pd.to_numeric(df[lon_column], errors="coerce").dropna()
    if len(lat) == 0 or len(lon) == 0:
        raise ValueError("No valid numeric coordinates found")

    result: Dict[str, Any] = {
        "resource": resource,
        "lat_column": lat_column,
        "lon_column": lon_column,
        "valid_point_count": min(len(lat), len(lon)),
        "bounding_box": {
            "min_lat": float(lat.min()),
            "max_lat": float(lat.max()),
            "min_lon": float(lon.min()),
            "max_lon": float(lon.max()),
        },
        "center": {"lat": float(lat.mean()), "lon": float(lon.mean())},
    }

    warnings = _bounds_warnings(lat.min(), lat.max(), lon.min(), lon.max())
    if warnings:
        result["warnings"] = warnings
    return result


@context_tool(toolset="tabular.spatial", requires=TabularContext)
def get_spatial_extent_from_tuple_column(
    ctx: TabularContext,
    resource: str,
    column: str,
    tuple_order: str = "lon_lat",
) -> Dict[str, Any]:
    """Calculate the bounding box from a single column of "(a, b)" float pairs.

    Use when coordinates are stored like "(-7.824283, 54.259247)" instead of in
    separate latitude/longitude columns. Set tuple_order to 'lat_lon' if the
    values are (latitude, longitude); the default assumes (longitude, latitude).
    """
    order = (tuple_order or "lon_lat").strip().lower()
    if order not in TUPLE_ORDERS:
        raise ValueError(f"Invalid tuple_order '{tuple_order}'; use lon_lat or lat_lon")

    df = ctx.read_resource(resource)
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found in resource '{resource}'")

    lons: List[float] = []
    lats: List[float] = []
    parse_failed = 0

    for val in df[column]:
        pair = parse_two_float_tuple(val)
        if pair is None:
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                parse_failed += 1
            continue
        first, second = pair
        if order == "lon_lat":
            lons.append(first)
            lats.append(second)
        else:
            lats.append(first)
            lons.append(second)

    if not lats or not lons:
        raise ValueError(
            f"No parseable (float, float) tuples found in column '{column}' "
            f"({parse_failed} unparseable rows)"
        )

    result: Dict[str, Any] = {
        "resource": resource,
        "column": column,
        "tuple_order": order,
        "valid_point_count": min(len(lats), len(lons)),
        "parse_failed_rows": parse_failed,
        "bounding_box": {
            "min_lat": float(min(lats)),
            "max_lat": float(max(lats)),
            "min_lon": float(min(lons)),
            "max_lon": float(max(lons)),
        },
        "center": {
            "lat": float(sum(lats) / len(lats)),
            "lon": float(sum(lons) / len(lons)),
        },
    }

    warnings = _bounds_warnings(min(lats), max(lats), min(lons), max(lons))
    if warnings:
        result["warnings"] = warnings
    return result
