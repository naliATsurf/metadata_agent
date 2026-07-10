"""Temporal analysis tools for row/column contexts."""

from typing import Any, Dict

import pandas as pd

from src.context.base_context import TabularContext
from src.tools.base import context_tool
from src.tools.tabular.detection import (
    detect_temporal_dtype,
    is_temporal_column_name,
)


def _coerce_datetime(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return pd.to_datetime(series, errors="coerce", format="mixed")


def _describe_granularity(median_diff) -> str:
    seconds = median_diff.total_seconds()
    if seconds < 1:
        return "sub-second"
    if seconds < 60:
        return "second"
    if seconds < 3600:
        return "minute"
    if seconds < 86400:
        return "hourly"
    if median_diff.days < 7:
        return "daily"
    if median_diff.days < 32:
        return "weekly/monthly"
    return "monthly+"


@context_tool(toolset="tabular.temporal", requires=TabularContext)
def detect_temporal_columns(ctx: TabularContext, resource: str = "") -> Dict[str, Any]:
    """Detect columns containing temporal (date/time) data in a resource.

    Returns column names, detected types, and sample values. Use the reported
    column names with analyze_temporal_column or get_temporal_extent.
    """
    resource = resource or ctx.resources[0]
    df = ctx.read_resource(resource)

    temporal_columns = {}
    for col in df.columns:
        detected_type = detect_temporal_dtype(df[col])
        name_suggests = is_temporal_column_name(col)

        if name_suggests or detected_type:
            temporal_columns[col] = {
                "name_suggests_temporal": name_suggests,
                "detected_type": detected_type,
                "sample_values": [str(v) for v in df[col].dropna().head(5)],
            }

    return {
        "resource": resource,
        "temporal_column_count": len(temporal_columns),
        "temporal_columns": temporal_columns,
    }


@context_tool(toolset="tabular.temporal", requires=TabularContext)
def analyze_temporal_column(
    ctx: TabularContext, resource: str, column: str
) -> Dict[str, Any]:
    """Analyze one temporal column in detail.

    Returns date range, apparent granularity, timezone, and parse success rate.
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
        "sample_values": [str(v) for v in series.dropna().head(5)],
    }

    try:
        parsed = _coerce_datetime(series)
    except Exception:
        parsed = None

    if parsed is None or not parsed.notna().any():
        result["parse_success_rate"] = 0
        result["note"] = "Could not parse as datetime"
        return result

    valid = parsed.dropna()
    result["parse_success_rate"] = len(valid) / len(series) if len(series) else 0
    result["date_range"] = {
        "min": str(valid.min()),
        "max": str(valid.max()),
        "span_days": (valid.max() - valid.min()).days,
    }

    if len(valid) > 1:
        median_diff = valid.sort_values().diff().dropna().median()
        result["apparent_granularity"] = _describe_granularity(median_diff)

    tz = getattr(valid.dtype, "tz", None)
    result["timezone"] = str(tz) if tz is not None else "none/naive"
    return result


@context_tool(toolset="tabular.temporal", requires=TabularContext)
def get_temporal_extent(
    ctx: TabularContext, resource: str, time_column: str
) -> Dict[str, Any]:
    """Calculate the temporal extent (start, end, duration) of a timestamp column.

    Also reports record counts by year and by month.
    """
    df = ctx.read_resource(resource)
    if time_column not in df.columns:
        raise ValueError(f"Column '{time_column}' not found in resource '{resource}'")

    series = df[time_column]
    valid = _coerce_datetime(series).dropna()
    if len(valid) == 0:
        raise ValueError(f"No valid datetime values found in column '{time_column}'")

    result: Dict[str, Any] = {
        "resource": resource,
        "time_column": time_column,
        "total_records": len(series),
        "valid_timestamps": len(valid),
        "null_timestamps": int(series.isnull().sum()),
        "temporal_extent": {
            "start": str(valid.min()),
            "end": str(valid.max()),
            "duration_days": (valid.max() - valid.min()).days,
        },
    }

    if len(valid) > 1:
        result["records_by_year"] = valid.dt.year.value_counts().sort_index().to_dict()
        result["records_by_month"] = valid.dt.month.value_counts().sort_index().to_dict()

    return result
