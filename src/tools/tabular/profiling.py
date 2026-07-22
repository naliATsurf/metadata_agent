"""Field-level profiling tools for row/column contexts."""

from typing import Any, Dict, List

from src.context.base_context import TabularContext
from src.tools.base import context_tool


@context_tool(toolset="tabular.profiling", requires=TabularContext, answers_field=True)
def get_field_names(ctx: TabularContext, resource: str = "") -> List[str]:
    """Get the field (column) names for a resource."""
    resource = resource or ctx.resources[0]
    return ctx.get_resource_info(resource).field_names


@context_tool(toolset="tabular.profiling", requires=TabularContext)
def get_field_types(ctx: TabularContext, resource: str = "") -> Dict[str, str]:
    """Get data types for all fields in a resource."""
    resource = resource or ctx.resources[0]
    info = ctx.get_resource_info(resource)
    return {field.name: field.dtype for field in info.fields}


@context_tool(toolset="tabular.profiling", requires=TabularContext)
def get_field_statistics(ctx: TabularContext, resource: str = "") -> Dict[str, Any]:
    """Get summary statistics for all fields in a resource."""
    resource = resource or ctx.resources[0]
    return ctx.read_resource(resource).describe(include="all").to_dict()


@context_tool(toolset="tabular.profiling", requires=TabularContext)
def get_missing_values(ctx: TabularContext, resource: str = "") -> Dict[str, int]:
    """Get the count of missing values per field."""
    resource = resource or ctx.resources[0]
    return ctx.read_resource(resource).isnull().sum().to_dict()


@context_tool(toolset="tabular.profiling", requires=TabularContext)
def get_unique_values(
    ctx: TabularContext, resource: str, field: str, limit: int = 100
) -> List[Any]:
    """Get the unique values of a specific field in a resource."""
    return ctx.get_field_values(resource, field, limit=limit)
