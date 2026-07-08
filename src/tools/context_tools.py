"""
Unified ExecutionContext Tools for the Multi-Agent System.
"""
import ast
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from langchain_core.tools import tool
from src.context.base_context import ContextType, TabularContext
from src.context.text_context import TextContext

_context_registry: Dict[str, Any] = {}


def register_context(key: str, context: Any) -> str:
    """
    Register an ExecutionContext in the global registry.
    """
    _context_registry[key] = context
    return key


def get_context(key: str) -> Any:
    """Get an ExecutionContext from the registry."""
    if key not in _context_registry:
        raise KeyError(f"ExecutionContext '{key}' not found in registry")
    return _context_registry[key]


def clear_registry():
    """Clear all registered ExecutionContexts."""
    _context_registry.clear()


def _get_tabular_context(key: str) -> TabularContext:
    """Get a context from the registry, requiring row/column access."""
    ctx = get_context(key)
    if not isinstance(ctx, TabularContext):
        raise TypeError(
            f"This tool requires a tabular context (CSV/SQLite), but "
            f"'{key}' is a '{ctx.context_type.value}' context."
        )
    return ctx


@tool
def get_context_overview(context_key: str) -> Dict[str, Any]:
    """
    Get an overview of the entire execution context including all resources.
    """
    try:
        ctx = get_context(context_key)

        # info.to_dict() is polymorphic: tabular resources report fields and
        # keys, text resources report word/char counts — no branching here.
        resources_info = {
            resource: ctx.get_resource_info(resource).to_dict()
            for resource in ctx.resources
        }

        relationships = [r.to_dict() for r in ctx.get_relationships()]

        return {
            "name": ctx.name,
            "context_type": ctx.context_type.value,
            "resource_count": len(ctx.resources),
            "resources": resources_info,
            "relationships": relationships,
            "relationship_count": len(relationships),
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def list_resources(context_key: str) -> List[str]:
    """
    List all resources in the execution context.
    """
    try:
        ctx = get_context(context_key)
        return ctx.resources
    except Exception as e:
        return [f"Error: {str(e)}"]


@tool
def get_context_schema(context_key: str) -> Dict[str, Any]:
    """
    Get the complete schema of the context including resources, fields, and relationships.
    """
    try:
        ctx = get_context(context_key)
        return ctx.get_schema()
    except Exception as e:
        return {"error": str(e)}


@tool
def get_resource_info(context_key: str, resource: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific resource.
    """
    try:
        ctx = get_context(context_key)
        info = ctx.get_resource_info(resource)
        return info.to_dict()
    except Exception as e:
        return {"error": str(e)}


@tool
def get_item_count(context_key: str, resource: str = "") -> int | str:
    """
    Get the number of items in a resource.
    """
    try:
        ctx = get_context(context_key)
        resource = resource or ctx.resources[0]
        info = ctx.get_resource_info(resource)
        return info.item_count
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_field_names(context_key: str, resource: str = "") -> List[str]:
    """
    Get the field names for a resource. Tabular contexts only.
    """
    try:
        ctx = _get_tabular_context(context_key)
        resource = resource or ctx.resources[0]
        info = ctx.get_resource_info(resource)
        return info.field_names
    except Exception as e:
        return [f"Error: {str(e)}"]


@tool
def get_field_types(context_key: str, resource: str = "") -> Dict[str, str]:
    """
    Get data types for all fields in a resource. Tabular contexts only.
    """
    try:
        ctx = _get_tabular_context(context_key)
        resource = resource or ctx.resources[0]
        info = ctx.get_resource_info(resource)
        return {field.name: field.dtype for field in info.fields}
    except Exception as e:
        return {"error": str(e)}


@tool
def get_sample_items(context_key: str, resource: str = "", n: int = 5) -> str:
    """
    Get a sample of items from a resource.

    This tool retrieves up to 'n' sample rows from the specified resource in the current context and
    returns them as a string table, allowing users or agents to preview the actual data in the resource.
    """
    try:
        ctx = get_context(context_key)
        resource = resource or ctx.resources[0]
        if isinstance(ctx, TextContext):
            chunks = ctx.get_chunks(resource)[:n]
            return "\n\n".join(chunk.text for chunk in chunks)
        df = _get_tabular_context(context_key).read_resource(resource, limit=n)
        return df.to_string()
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_field_statistics(context_key: str, resource: str = "") -> Dict[str, Any]:
    """
    Get statistics for all fields in a resource.
    """
    try:
        ctx = _get_tabular_context(context_key)
        resource = resource or ctx.resources[0]
        df = ctx.read_resource(resource)
        return df.describe(include="all").to_dict()
    except Exception as e:
        return {"error": str(e)}


@tool
def get_missing_values(context_key: str, resource: str = "") -> Dict[str, int | str]:
    """
    Get count of missing values per field.
    """
    try:
        ctx = _get_tabular_context(context_key)
        resource = resource or ctx.resources[0]
        df = ctx.read_resource(resource)
        return df.isnull().sum().to_dict()
    except Exception as e:
        return {"error": str(e)}


@tool
def get_unique_values(
    context_key: str, resource: str, field: str, limit: int = 100
) -> List[Any]:
    """
    Get unique values from a specific field.
    """
    try:
        ctx = _get_tabular_context(context_key)
        values = ctx.get_field_values(resource, field, limit=limit)
        return values
    except Exception as e:
        return [f"Error: {str(e)}"]


@tool
def get_relationships(context_key: str) -> List[Dict[str, Any]]:
    """
    Get all discovered or defined relationships between resources.
    """
    try:
        ctx = get_context(context_key)
        return [r.to_dict() for r in ctx.get_relationships()]
    except Exception as e:
        return [{"error": str(e)}]


# =============================================================================
# Spatial-Temporal Analysis Tools
# =============================================================================

# Common temporal patterns for detection
TEMPORAL_PATTERNS = [
    r"date",
    r"time",
    r"timestamp",
    r"datetime",
    r"created",
    r"updated",
    r"modified",
    r"start",
    r"end",
    r"begin",
    r"expire",
    r"valid",
    r"year",
    r"month",
    r"day",
    r"hour",
    r"minute",
    r"second",
    r"period",
    r"duration",
    r"_at$",
    r"_on$",
    r"_dt$",
]

# Common spatial patterns for detection
SPATIAL_PATTERNS = [
    r"lat(?:itude)?",
    r"lon(?:g(?:itude)?)?",
    r"coord",
    r"geo",
    r"location",
    r"position",
    r"point",
    r"polygon",
    r"geometry",
    r"geom",
    r"wkt",
    r"wkb",
    r"x_?coord",
    r"y_?coord",
    r"easting",
    r"northing",
    r"spatial",
    r"place",
    r"address",
    r"city",
    r"state",
    r"country",
    r"zip",
    r"postal",
    r"region",
    r"bbox",
    r"bounds",
    r"extent",
]


def _is_temporal_column_name(column_name: str) -> bool:
    """Check if column name suggests temporal data."""
    name_lower = column_name.lower()
    for pattern in TEMPORAL_PATTERNS:
        if re.search(pattern, name_lower):
            return True
    return False


def _is_spatial_column_name(column_name: str) -> bool:
    """Check if column name suggests spatial data."""
    name_lower = column_name.lower()
    for pattern in SPATIAL_PATTERNS:
        if re.search(pattern, name_lower):
            return True
    return False


def _detect_temporal_dtype(series: pd.Series) -> Optional[str]:
    """Detect if a series contains temporal data based on dtype and values."""
    # Check pandas datetime types
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime64"
    
    # Check timedelta
    if pd.api.types.is_timedelta64_dtype(series):
        return "timedelta64"
    
    # Check object/string dtype for parseable dates
    if series.dtype == object or str(series.dtype) == "string":
        sample = series.dropna().head(100)
        if len(sample) == 0:
            return None
        
        # Try to parse as datetime
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            valid_ratio = parsed.notna().sum() / len(sample)
            if valid_ratio > 0.8:
                return "datetime_string"
        except Exception:
            pass
    
    return None


def _detect_coordinate_values(series: pd.Series) -> Optional[str]:
    """Detect if numeric values look like coordinates."""
    if not pd.api.types.is_numeric_dtype(series):
        return None
    
    sample = series.dropna()
    if len(sample) == 0:
        return None
    
    min_val, max_val = sample.min(), sample.max()
    
    # Latitude range: -90 to 90
    if -90 <= min_val and max_val <= 90:
        return "possible_latitude"
    
    # Longitude range: -180 to 180
    if -180 <= min_val and max_val <= 180:
        return "possible_longitude"
    
    return None


def _detect_wkt_geometry(series: pd.Series) -> Optional[str]:
    """Detect if string values contain WKT geometry."""
    if series.dtype != object and str(series.dtype) != "string":
        return None
    
    sample = series.dropna().head(50)
    if len(sample) == 0:
        return None
    
    wkt_patterns = [
        r"^POINT\s*\(",
        r"^LINESTRING\s*\(",
        r"^POLYGON\s*\(",
        r"^MULTIPOINT\s*\(",
        r"^MULTILINESTRING\s*\(",
        r"^MULTIPOLYGON\s*\(",
        r"^GEOMETRYCOLLECTION\s*\(",
    ]
    
    wkt_count = 0
    for val in sample:
        val_upper = str(val).upper().strip()
        for pattern in wkt_patterns:
            if re.match(pattern, val_upper):
                wkt_count += 1
                break
    
    if wkt_count / len(sample) > 0.5:
        return "wkt_geometry"
    
    return None


_TUPLE_PAIR_RE = re.compile(
    r"^\(\s*([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*,\s*"
    r"([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*\)$"
)


def _try_parse_two_float_tuple(val: Any) -> Optional[Tuple[float, float]]:
    """Parse values like '(-7.82, 54.26)' or '(lon, lat)' into two floats."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    m = _TUPLE_PAIR_RE.match(s)
    if m:
        return float(m.group(1)), float(m.group(2))
    try:
        t = ast.literal_eval(s)
        if isinstance(t, (list, tuple)) and len(t) == 2:
            return float(t[0]), float(t[1])
    except (ValueError, SyntaxError, TypeError):
        pass
    return None


def _sample_tuple_parse_rate(series: pd.Series, max_sample: int = 80) -> Tuple[int, int]:
    """Return (parsed_count, tried_count) on non-null head sample."""
    sample = series.dropna().head(max_sample)
    tried = len(sample)
    if tried == 0:
        return 0, 0
    parsed = sum(1 for v in sample if _try_parse_two_float_tuple(v) is not None)
    return parsed, tried


@tool
def detect_temporal_columns(context_key: str, resource: str = "") -> Dict[str, Any]:
    """
    Detect columns that contain temporal (date/time) data in a resource.
    Returns column names, detected types, and temporal characteristics.
    """
    try:
        ctx = _get_tabular_context(context_key)
        resource = resource or ctx.resources[0]
        df = ctx.read_resource(resource)

        temporal_columns = {}
        
        for col in df.columns:
            col_info = {
                "name_suggests_temporal": _is_temporal_column_name(col),
                "detected_type": None,
                "sample_values": [],
            }
            
            # Detect type from data
            detected_type = _detect_temporal_dtype(df[col])
            if detected_type:
                col_info["detected_type"] = detected_type
            
            # If either name or type suggests temporal, include it
            if col_info["name_suggests_temporal"] or col_info["detected_type"]:
                # Add sample values
                sample = df[col].dropna().head(5).tolist()
                col_info["sample_values"] = [str(v) for v in sample]
                temporal_columns[col] = col_info
        
        return {
            "resource": resource,
            "temporal_column_count": len(temporal_columns),
            "temporal_columns": temporal_columns,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def analyze_temporal_column(
    context_key: str, resource: str, column: str
) -> Dict[str, Any]:
    """
    Analyze a specific temporal column in detail.
    Returns date range, granularity, format patterns, and temporal coverage.
    """
    try:
        ctx = _get_tabular_context(context_key)
        df = ctx.read_resource(resource)
        
        if column not in df.columns:
            return {"error": f"Column '{column}' not found in resource '{resource}'"}
        
        series = df[column]
        result = {
            "column": column,
            "resource": resource,
            "total_values": len(series),
            "null_count": series.isnull().sum(),
            "non_null_count": series.notna().sum(),
        }
        
        # Try to parse as datetime
        parsed = None
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = series
        else:
            try:
                parsed = pd.to_datetime(series, errors="coerce", format="mixed")
            except Exception:
                pass
        
        if parsed is not None and parsed.notna().any():
            valid_dates = parsed.dropna()
            result["parse_success_rate"] = len(valid_dates) / len(series) if len(series) > 0 else 0
            
            if len(valid_dates) > 0:
                result["date_range"] = {
                    "min": str(valid_dates.min()),
                    "max": str(valid_dates.max()),
                    "span_days": (valid_dates.max() - valid_dates.min()).days,
                }
                
                # Detect granularity
                if len(valid_dates) > 1:
                    diffs = valid_dates.sort_values().diff().dropna()
                    median_diff = diffs.median()
                    
                    if median_diff.total_seconds() < 1:
                        result["apparent_granularity"] = "sub-second"
                    elif median_diff.total_seconds() < 60:
                        result["apparent_granularity"] = "second"
                    elif median_diff.total_seconds() < 3600:
                        result["apparent_granularity"] = "minute"
                    elif median_diff.total_seconds() < 86400:
                        result["apparent_granularity"] = "hourly"
                    elif median_diff.days < 7:
                        result["apparent_granularity"] = "daily"
                    elif median_diff.days < 32:
                        result["apparent_granularity"] = "weekly/monthly"
                    else:
                        result["apparent_granularity"] = "monthly+"
                
                # Check for timezone info
                if hasattr(valid_dates.dtype, "tz") and valid_dates.dtype.tz is not None:
                    result["timezone"] = str(valid_dates.dtype.tz)
                else:
                    result["timezone"] = "none/naive"
        else:
            result["parse_success_rate"] = 0
            result["note"] = "Could not parse as datetime"
        
        # Sample original values
        result["sample_values"] = [str(v) for v in series.dropna().head(5).tolist()]
        
        return result
    except Exception as e:
        return {"error": str(e)}


@tool
def detect_spatial_columns(context_key: str, resource: str = "") -> Dict[str, Any]:
    """
    Detect columns that contain spatial (geographic/coordinate) data in a resource.
    Returns column names, detected types, and spatial characteristics.

    Also reports `tuple_coord_columns`: string columns whose values look like
    ``"(a, b)"`` pairs of floats (e.g. survey ``tuple_coords`` as ``(lon, lat)``).
    Use ``get_spatial_extent_from_tuple_column`` for extent on those columns.
    """
    try:
        ctx = _get_tabular_context(context_key)
        resource = resource or ctx.resources[0]
        df = ctx.read_resource(resource)

        spatial_columns = {}
        coordinate_pairs = []
        tuple_coord_columns: List[Dict[str, Any]] = []

        for col in df.columns:
            col_info = {
                "name_suggests_spatial": _is_spatial_column_name(col),
                "detected_type": None,
                "sample_values": [],
            }

            # Check for WKT geometry
            wkt_type = _detect_wkt_geometry(df[col])
            if wkt_type:
                col_info["detected_type"] = wkt_type

            # Check for coordinate values
            coord_type = _detect_coordinate_values(df[col])
            if coord_type:
                col_info["detected_type"] = coord_type

            # Tuple string pairs "(lon, lat)" — common in ecology / BMS exports
            if (
                col_info["detected_type"] is None
                and (df[col].dtype == object or str(df[col].dtype) == "string")
            ):
                parsed, tried = _sample_tuple_parse_rate(df[col])
                if tried > 0 and parsed / tried >= 0.75:
                    col_info["detected_type"] = "two_float_tuple_string"
                    col_info["tuple_order_hint"] = (
                        "lon_lat"
                    )  # default; many UK/EU grids use (lon, lat)
                    tuple_coord_columns.append(
                        {
                            "column": col,
                            "tuple_order": "lon_lat",
                            "sample_parse_rate": round(parsed / tried, 3),
                            "note": "Default tuple_order is lon_lat; pass tuple_order='lat_lon' to "
                            "get_spatial_extent_from_tuple_column if your file uses (lat, lon).",
                        }
                    )

            # If either name or type suggests spatial, include it
            if col_info["name_suggests_spatial"] or col_info["detected_type"]:
                sample = df[col].dropna().head(5).tolist()
                col_info["sample_values"] = [str(v) for v in sample]
                spatial_columns[col] = col_info

        # Try to detect lat/lon pairs
        lat_cols = [
            c
            for c, info in spatial_columns.items()
            if info.get("detected_type") == "possible_latitude"
            or re.search(r"lat", c.lower())
        ]
        lon_cols = [
            c
            for c, info in spatial_columns.items()
            if info.get("detected_type") == "possible_longitude"
            or re.search(r"lon", c.lower())
        ]

        if lat_cols and lon_cols:
            coordinate_pairs = [{"latitude": lat_cols[0], "longitude": lon_cols[0]}]

        return {
            "resource": resource,
            "spatial_column_count": len(spatial_columns),
            "spatial_columns": spatial_columns,
            "detected_coordinate_pairs": coordinate_pairs,
            "tuple_coord_columns": tuple_coord_columns,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def analyze_spatial_column(
    context_key: str, resource: str, column: str
) -> Dict[str, Any]:
    """
    Analyze a specific spatial column in detail.
    Returns coordinate ranges, geometry types, and spatial extent information.
    """
    try:
        ctx = _get_tabular_context(context_key)
        df = ctx.read_resource(resource)
        
        if column not in df.columns:
            return {"error": f"Column '{column}' not found in resource '{resource}'"}
        
        series = df[column]
        result = {
            "column": column,
            "resource": resource,
            "total_values": len(series),
            "null_count": series.isnull().sum(),
            "non_null_count": series.notna().sum(),
            "dtype": str(series.dtype),
        }
        
        # Analyze based on data type
        if pd.api.types.is_numeric_dtype(series):
            valid = series.dropna()
            if len(valid) > 0:
                result["value_range"] = {
                    "min": float(valid.min()),
                    "max": float(valid.max()),
                    "mean": float(valid.mean()),
                }
                
                # Determine if looks like latitude or longitude
                min_val, max_val = valid.min(), valid.max()
                if -90 <= min_val and max_val <= 90:
                    result["coordinate_type_hint"] = "latitude"
                elif -180 <= min_val and max_val <= 180:
                    result["coordinate_type_hint"] = "longitude"
                else:
                    result["coordinate_type_hint"] = "projected_or_other"
        
        # Check for WKT/geometry strings
        elif series.dtype == object or str(series.dtype) == "string":
            wkt_type = _detect_wkt_geometry(series)
            if wkt_type:
                result["geometry_format"] = "WKT"
                
                # Try to extract geometry types
                sample = series.dropna().head(100)
                geometry_types = {}
                for val in sample:
                    val_upper = str(val).upper().strip()
                    for gtype in ["POINT", "LINESTRING", "POLYGON", 
                                  "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON"]:
                        if val_upper.startswith(gtype):
                            geometry_types[gtype] = geometry_types.get(gtype, 0) + 1
                            break
                
                result["geometry_types"] = geometry_types
        
        # Sample values
        result["sample_values"] = [str(v) for v in series.dropna().head(5).tolist()]
        
        return result
    except Exception as e:
        return {"error": str(e)}


@tool
def get_spatial_extent(
    context_key: str, resource: str, lat_column: str, lon_column: str
) -> Dict[str, Any]:
    """
    Calculate the spatial bounding box extent from latitude and longitude columns.
    Returns min/max coordinates and center point.
    """
    try:
        ctx = _get_tabular_context(context_key)
        df = ctx.read_resource(resource)

        if lat_column not in df.columns:
            return {"error": f"Column '{lat_column}' not found"}
        if lon_column not in df.columns:
            return {"error": f"Column '{lon_column}' not found"}
        
        lat = pd.to_numeric(df[lat_column], errors="coerce").dropna()
        lon = pd.to_numeric(df[lon_column], errors="coerce").dropna()
        
        if len(lat) == 0 or len(lon) == 0:
            return {"error": "No valid numeric coordinates found"}
        
        result = {
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
            "center": {
                "lat": float(lat.mean()),
                "lon": float(lon.mean()),
            },
        }
        
        # Validate coordinate ranges
        warnings = []
        if lat.min() < -90 or lat.max() > 90:
            warnings.append("Latitude values outside valid range [-90, 90]")
        if lon.min() < -180 or lon.max() > 180:
            warnings.append("Longitude values outside valid range [-180, 180]")
        
        if warnings:
            result["warnings"] = warnings
        
        return result
    except Exception as e:
        return {"error": str(e)}


@tool
def get_spatial_extent_from_tuple_column(
    context_key: str,
    resource: str,
    column: str,
    tuple_order: str = "lon_lat",
) -> Dict[str, Any]:
    """
    Bounding box from a single column of ``(a, b)`` float pairs (string or tuple).

    Use when coordinates are stored like ``(-7.824283, 54.259247)`` instead of
    separate latitude/longitude columns (e.g. some BMS / survey exports).

    Args:
        context_key: Registered ExecutionContext key.
        resource: Resource name in the context.
        column: Column containing ``(first, second)`` pairs.
        tuple_order: ``lon_lat`` if values are ``(longitude, latitude)`` (default),
            or ``lat_lon`` if values are ``(latitude, longitude)``.
    """
    try:
        order = (tuple_order or "lon_lat").strip().lower()
        if order not in ("lon_lat", "lat_lon"):
            return {"error": f"Invalid tuple_order '{tuple_order}'; use lon_lat or lat_lon"}

        ctx = _get_tabular_context(context_key)
        df = ctx.read_resource(resource)
        if column not in df.columns:
            return {"error": f"Column '{column}' not found"}

        series = df[column]
        lons: List[float] = []
        lats: List[float] = []
        parse_failed = 0

        for val in series:
            pair = _try_parse_two_float_tuple(val)
            if pair is None:
                if val is not None and not (isinstance(val, float) and pd.isna(val)):
                    parse_failed += 1
                continue
            a, b = pair
            if order == "lon_lat":
                lons.append(a)
                lats.append(b)
            else:
                lats.append(a)
                lons.append(b)

        if len(lats) == 0 or len(lons) == 0:
            return {
                "error": "No parseable (float, float) tuples found",
                "column": column,
                "tuple_order": order,
                "parse_failed_rows": parse_failed,
            }

        result = {
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

        warnings = []
        if result["bounding_box"]["min_lat"] < -90 or result["bounding_box"]["max_lat"] > 90:
            warnings.append("Latitude values outside valid range [-90, 90]")
        if result["bounding_box"]["min_lon"] < -180 or result["bounding_box"]["max_lon"] > 180:
            warnings.append("Longitude values outside valid range [-180, 180]")
        if warnings:
            result["warnings"] = warnings

        return result
    except Exception as e:
        return {"error": str(e)}


@tool
def get_temporal_extent(
    context_key: str, resource: str, time_column: str
) -> Dict[str, Any]:
    """
    Calculate the temporal extent from a timestamp column.
    Returns time range, coverage statistics, and temporal distribution info.
    """
    try:
        ctx = _get_tabular_context(context_key)
        df = ctx.read_resource(resource)

        if time_column not in df.columns:
            return {"error": f"Column '{time_column}' not found"}
        
        # Parse as datetime
        series = df[time_column]
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = series
        else:
            parsed = pd.to_datetime(series, errors="coerce", format="mixed")
        
        valid = parsed.dropna()
        
        if len(valid) == 0:
            return {"error": "No valid datetime values found"}
        
        result = {
            "resource": resource,
            "time_column": time_column,
            "total_records": len(series),
            "valid_timestamps": len(valid),
            "null_timestamps": series.isnull().sum(),
            "temporal_extent": {
                "start": str(valid.min()),
                "end": str(valid.max()),
                "duration_days": (valid.max() - valid.min()).days,
            },
        }
        
        # Add temporal distribution info
        if len(valid) > 1:
            by_year = valid.dt.year.value_counts().sort_index()
            result["records_by_year"] = by_year.to_dict()
            
            by_month = valid.dt.month.value_counts().sort_index()
            result["records_by_month"] = by_month.to_dict()
        
        return result
    except Exception as e:
        return {"error": str(e)}


def get_all_context_tools() -> List:
    """Return list of all ExecutionContext tools."""
    return [
        get_context_overview,
        list_resources,
        get_context_schema,
        get_resource_info,
        get_item_count,
        get_field_names,
        get_field_types,
        get_sample_items,
        get_field_statistics,
        get_missing_values,
        get_unique_values,
        get_relationships,
        # Spatial-temporal tools
        detect_temporal_columns,
        analyze_temporal_column,
        detect_spatial_columns,
        analyze_spatial_column,
        get_spatial_extent,
        get_spatial_extent_from_tuple_column,
        get_temporal_extent,
    ]


def _get_tool_context_compatibility() -> Dict[str, Set[ContextType]]:
    """Map tool names to supported context types."""
    csv_types = {ContextType.SINGLE_CSV, ContextType.MULTI_CSV}
    return {
        "get_context_overview": csv_types,
        "list_resources": csv_types,
        "get_context_schema": csv_types,
        "get_resource_info": csv_types,
        "get_item_count": csv_types,
        "get_field_names": csv_types,
        "get_field_types": csv_types,
        "get_sample_items": csv_types,
        "get_field_statistics": csv_types,
        "get_missing_values": csv_types,
        "get_unique_values": csv_types,
        "get_relationships": {ContextType.MULTI_CSV},
        "detect_temporal_columns": csv_types,
        "analyze_temporal_column": csv_types,
        "detect_spatial_columns": csv_types,
        "analyze_spatial_column": csv_types,
        "get_spatial_extent": csv_types,
        "get_spatial_extent_from_tuple_column": csv_types,
        "get_temporal_extent": csv_types,
    }


def filter_tools_by_context_type(tools: List, context_type: ContextType) -> List:
    """
    Filter tool list based on supported context types.

    Unregistered tools are allowed by default for backward compatibility.
    """
    compatibility = _get_tool_context_compatibility()
    filtered = []
    for tool_fn in tools:
        supported = compatibility.get(tool_fn.name)
        if supported is None or context_type in supported:
            filtered.append(tool_fn)
    return filtered


def get_tools_for_context_type(context_type: ContextType) -> List:
    """Return all context tools compatible with the given context type."""
    return filter_tools_by_context_type(get_all_context_tools(), context_type)


def get_single_csv_tools() -> List:
    """Return tools appropriate for single CSV contexts."""
    return get_tools_for_context_type(ContextType.SINGLE_CSV)


def get_multi_csv_tools() -> List:
    """Return tools appropriate for multi CSV contexts."""
    return get_tools_for_context_type(ContextType.MULTI_CSV)