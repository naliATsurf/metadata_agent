"""
Modality-agnostic tools.

These ask only what the :class:`ExecutionContext` contract can answer, so they
work on every modality — tabular, text, and whatever comes next — without
modification.
"""

from typing import Any, Dict, List

from src.context.base_context import ExecutionContext
from src.tools.base import context_tool


@context_tool(toolset="universal")
def get_context_overview(ctx: ExecutionContext) -> Dict[str, Any]:
    """Get an overview of the entire execution context including all resources."""
    # info.to_dict() is polymorphic: tabular resources report fields and keys,
    # text resources report word/char counts — no branching here.
    relationships = [r.to_dict() for r in ctx.get_relationships()]
    return {
        "name": ctx.name,
        "context_type": ctx.context_type.value,
        "resource_count": len(ctx.resources),
        "resources": {
            resource: ctx.get_resource_info(resource).to_dict()
            for resource in ctx.resources
        },
        "relationships": relationships,
        "relationship_count": len(relationships),
    }


@context_tool(toolset="universal")
def list_resources(ctx: ExecutionContext) -> List[str]:
    """List all resources in the execution context."""
    return ctx.resources


@context_tool(toolset="universal")
def get_context_schema(ctx: ExecutionContext) -> Dict[str, Any]:
    """Get the complete schema of the context including resources, fields, and relationships."""
    return ctx.get_schema()


@context_tool(toolset="universal")
def get_resource_info(ctx: ExecutionContext, resource: str) -> Dict[str, Any]:
    """Get detailed information about a specific resource."""
    return ctx.get_resource_info(resource).to_dict()


@context_tool(toolset="universal", answers_field=True)
def get_item_count(ctx: ExecutionContext, resource: str = "") -> int:
    """Get the number of items in a resource: the row or record count for a table,
    the chunk count for a document."""
    resource = resource or ctx.resources[0]
    return ctx.get_resource_info(resource).item_count


@context_tool(toolset="universal")
def get_sample_items(ctx: ExecutionContext, resource: str = "", n: int = 5) -> str:
    """Get a sample of up to n items from a resource, to preview the actual data.

    Returns rows for tabular resources and text chunks for documents.
    """
    return ctx.preview(resource or ctx.resources[0], n=n)


@context_tool(
    toolset="universal.relationships",
    available_when=lambda ctx: ctx.is_multi_resource,
)
def get_relationships(ctx: ExecutionContext) -> List[Dict[str, Any]]:
    """Get all discovered or defined relationships between resources."""
    return [r.to_dict() for r in ctx.get_relationships()]
