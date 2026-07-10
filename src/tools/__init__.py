"""
Tools module for the multi-agent system.

Tools are declared with :func:`~src.tools.base.context_tool`, which records the
context capability each one needs. Gating is then a capability check rather
than a hand-maintained table, so a new modality is unlocked by adding a context
subclass, not by editing a list here.

Importing this package registers every tool. Query them with
:func:`~src.tools.base.tools_for` (what can run against this context?) or
:func:`~src.tools.base.resolve_toolsets` (what did this player ask for?).
"""

from src.tools import universal  # noqa: F401  (registers universal tools)
from src.tools import tabular  # noqa: F401  (registers tabular tools)

from src.tools.base import (
    all_tools,
    clear_registry,
    context_tool,
    get_context,
    is_auto_fireable,
    is_resource_scoped,
    register_context,
    registered_toolsets,
    requires_of,
    resolve_toolsets,
    tool_meta,
    tools_for,
    toolset_of,
)

__all__ = [
    "all_tools",
    "clear_registry",
    "context_tool",
    "get_context",
    "is_auto_fireable",
    "is_resource_scoped",
    "register_context",
    "registered_toolsets",
    "requires_of",
    "resolve_toolsets",
    "tabular",
    "tool_meta",
    "tools_for",
    "toolset_of",
    "universal",
]
