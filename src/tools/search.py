"""The search tool — the field router's locate primitive.

One tool, gated on the :class:`~src.context.base_context.Searchable` capability,
so it is offered to any modality that can rank its own contents (tabular columns,
text spans) without the router branching on context type. Because it funnels
through the standard tool boundary, every call is captured in the evidence ledger
with its caller (``phase="route"`` once the router drives it).
"""

from typing import Any, Dict, List

from src.context.base_context import Searchable
from src.tools.base import context_tool


@context_tool(toolset="search", requires=Searchable)
def search_context(
    ctx: Searchable, query: str, resource: str = "", k: int = 5
) -> List[Dict[str, Any]]:
    """Locate candidate evidence for a query.

    Returns up to ``k`` ranked candidates — columns to compute on
    (``computed_column``) or text spans to quote (``quoted_span``) — best score
    first. May be empty: that is the signal the context holds no source for the
    query, not an error.
    """
    return [ref.to_dict() for ref in ctx.search(query, resource=resource, k=k)]
