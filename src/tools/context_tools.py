"""
Unified ExecutionContext Tools for the Multi-Agent System.
"""
from typing import Any, Dict, List

from langchain_core.tools import tool

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


@tool
def get_context_overview(context_key: str) -> Dict[str, Any]:
    """
    Get an overview of the entire execution context including all resources.
    """
    try:
        ctx = get_context(context_key)

        resources_info = {}
        for resource in ctx.resources:
            info = ctx.get_resource_info(resource)
            resources_info[resource] = {
                "item_count": info.item_count,
                "field_count": info.field_count,
                "fields": info.field_names,
                "primary_key": info.primary_key,
            }

        relationships = [r.to_dict() for r in ctx.get_relationships()]

        return {
            "name": ctx.name,
            "context_type": ctx.context_type.value,
            "is_multi_resource": ctx.is_multi_resource,
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
def get_item_count(context_key: str, resource: str = None) -> int:
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
def get_field_names(context_key: str, resource: str = None) -> List[str]:
    """
    Get the field names for a resource.
    """
    try:
        ctx = get_context(context_key)
        resource = resource or ctx.resources[0]
        info = ctx.get_resource_info(resource)
        return info.field_names
    except Exception as e:
        return [f"Error: {str(e)}"]


@tool
def get_field_types(context_key: str, resource: str = None) -> Dict[str, str]:
    """
    Get data types for all fields in a resource.
    """
    try:
        ctx = get_context(context_key)
        resource = resource or ctx.resources[0]
        info = ctx.get_resource_info(resource)
        return {field.name: field.dtype for field in info.fields}
    except Exception as e:
        return {"error": str(e)}


@tool
def get_sample_items(context_key: str, resource: str = None, n: int = 5) -> str:
    """
    Get a sample of items from a resource.
    """
    try:
        ctx = get_context(context_key)
        resource = resource or ctx.resources[0]
        df = ctx.read_resource(resource, limit=n)
        return df.to_string()
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_field_statistics(context_key: str, resource: str = None) -> Dict[str, Any]:
    """
    Get statistics for all fields in a resource.
    """
    try:
        ctx = get_context(context_key)
        resource = resource or ctx.resources[0]
        df = ctx.read_resource(resource)
        return df.describe(include="all").to_dict()
    except Exception as e:
        return {"error": str(e)}


@tool
def get_missing_values(context_key: str, resource: str = None) -> Dict[str, int]:
    """
    Get count of missing values per field.
    """
    try:
        ctx = get_context(context_key)
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
        ctx = get_context(context_key)
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
    ]


def get_single_resource_tools() -> List:
    """Return tools appropriate for single-resource contexts."""
    return get_all_context_tools()


def get_multi_resource_tools() -> List:
    """Return tools appropriate for multi-resource contexts."""
    return get_all_context_tools()