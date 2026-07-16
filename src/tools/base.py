"""
Tool registry, capability gating, and the ``@context_tool`` decorator.

Tools declare the *capability* they need from a context, not the context type
they run on::

    @context_tool(toolset="tabular.profiling", requires=TabularContext)
    def get_field_names(ctx: TabularContext, resource: str = "") -> List[str]:
        ...

Three facts then follow from that one declaration, so no side table can drift
out of sync with the tools:

``requires``
    Gating. :func:`tools_for` offers a tool to a context iff the context is an
    instance of the required capability class. Adding a modality means adding a
    context subclass — never editing a compatibility table.

``resource_scoped``
    Derived from the signature: does the tool accept a ``resource`` argument?
    Resource-scoped tools run once per target resource; the rest run once per
    context.

``auto_fireable``
    Derived from the signature: are ``context_key`` and ``resource`` the only
    *required* arguments? If so a runner can invoke the tool with no help from
    a model. Tools needing a ``column`` or ``field`` cannot be guessed at and
    must be offered to the model for it to call with arguments.

Tool bodies receive a resolved, type-checked context as their first argument
and raise on failure. Resolution, capability enforcement, and the
``context_key`` indirection required to make tools LLM-callable all live here
rather than being restated in every tool body.
"""

from __future__ import annotations

import functools
import inspect
from fnmatch import fnmatch
from typing import Any, Callable, Dict, List, Optional, Type

from langchain_core.tools import BaseTool, tool as _lc_tool

from src.context.base_context import ExecutionContext
from src.provenance import clear_evidence, record_evidence

# Arguments the runner can always supply itself. Any other required argument
# (column, field, lat_column, ...) means the tool needs a model to call it.
RUNNER_SUPPLIED_ARGS = frozenset({"context_key", "resource"})

_TOOL_REGISTRY: List[BaseTool] = []
_CONTEXT_REGISTRY: Dict[str, ExecutionContext] = {}


# ---------------------------------------------------------------------------
# Context registry
#
# Tools take a ``context_key`` string rather than a context object, because
# tool arguments must be JSON-serializable for a model to supply them.
# ---------------------------------------------------------------------------


def register_context(key: str, context: ExecutionContext) -> str:
    """Register an ExecutionContext so tools can resolve it by key."""
    _CONTEXT_REGISTRY[key] = context
    return key


def get_context(key: str) -> ExecutionContext:
    """Resolve a registered ExecutionContext."""
    if key not in _CONTEXT_REGISTRY:
        raise KeyError(f"ExecutionContext '{key}' not found in registry")
    return _CONTEXT_REGISTRY[key]


def clear_registry() -> None:
    """Clear all registered ExecutionContexts and their captured evidence."""
    _CONTEXT_REGISTRY.clear()
    clear_evidence()


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


def tool_meta(t: BaseTool) -> Dict[str, Any]:
    """Return the ``@context_tool`` metadata attached to a tool."""
    return t.metadata or {}


def requires_of(t: BaseTool) -> Type[ExecutionContext]:
    return tool_meta(t).get("requires", ExecutionContext)


def is_auto_fireable(t: BaseTool) -> bool:
    """True when a runner can invoke the tool without a model choosing args."""
    return tool_meta(t).get("auto_fireable", False)


def is_resource_scoped(t: BaseTool) -> bool:
    """True when the tool takes a ``resource`` and runs once per resource."""
    return tool_meta(t).get("resource_scoped", False)


def toolset_of(t: BaseTool) -> str:
    return tool_meta(t).get("toolset", "")


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def _build_llm_facing_function(
    fn: Callable, requires: Type[ExecutionContext]
) -> Callable:
    """Turn ``fn(ctx, ...)`` into ``wrapper(context_key: str, ...)``.

    The model-facing signature takes a string key; the body receives a resolved
    context already checked against ``requires``. LangChain infers the tool's
    argument schema from the rewritten signature and annotations.
    """
    original_sig = inspect.signature(fn)
    params = list(original_sig.parameters.values())

    if not params:
        raise TypeError(f"{fn.__name__} must take a context as its first argument")

    context_key_param = inspect.Parameter(
        "context_key", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str
    )
    new_sig = original_sig.replace(parameters=[context_key_param] + params[1:])

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = new_sig.bind(*args, **kwargs)
        bound.apply_defaults()
        key = bound.arguments.pop("context_key")

        ctx = get_context(key)
        if not isinstance(ctx, requires):
            raise TypeError(
                f"Tool '{fn.__name__}' requires a {requires.__name__}, but "
                f"'{key}' is a {type(ctx).__name__} "
                f"({ctx.context_type.value} context)."
            )
        result = fn(ctx, **bound.arguments)

        # Provenance: capture the fact this tool just produced, keyed by the run's
        # context. Every tool passes through here, so this one site instruments
        # the whole tool surface. Recording is a pure append and cannot alter the
        # returned value. Runs after ``fn`` so a raising tool records nothing.
        call_args = {k: v for k, v in bound.arguments.items() if k != "resource"}
        record_evidence(
            context_key=key,
            tool=fn.__name__,
            resource=bound.arguments.get("resource"),
            args=call_args,
            result=result,
        )
        return result

    # LangChain reads both of these to build the argument schema; functools.wraps
    # copies the original annotations (which name ``ctx``), so replace them.
    annotations = dict(getattr(fn, "__annotations__", {}))
    annotations.pop(params[0].name, None)
    annotations["context_key"] = str
    wrapper.__annotations__ = annotations
    wrapper.__signature__ = new_sig
    return wrapper


def _derive_dispatch_flags(t: BaseTool) -> Dict[str, bool]:
    """Read the tool's own argument schema to decide how it can be called."""
    schema = t.args_schema.model_json_schema()
    properties = set(schema.get("properties", {}))
    required = set(schema.get("required", []))
    return {
        "resource_scoped": "resource" in properties,
        "auto_fireable": not (required - RUNNER_SUPPLIED_ARGS),
    }


def context_tool(
    *,
    toolset: str,
    requires: Type[ExecutionContext] = ExecutionContext,
    available_when: Optional[Callable[[ExecutionContext], bool]] = None,
):
    """Register a context tool and declare the capability it needs.

    Args:
        toolset: Dotted group name used by player configs, e.g.
            ``"tabular.spatial"``. Players request toolsets, not tools.
        requires: The context capability class the tool needs. Defaults to
            :class:`ExecutionContext`, meaning the tool works on any modality.
        available_when: Optional predicate for concerns capability cannot
            express — cardinality, for instance. Use sparingly.
    """

    def decorator(fn: Callable) -> BaseTool:
        llm_facing = _build_llm_facing_function(fn, requires)
        t: BaseTool = _lc_tool(llm_facing)
        t.metadata = {
            "toolset": toolset,
            "requires": requires,
            "available_when": available_when,
            **_derive_dispatch_flags(t),
        }
        _TOOL_REGISTRY.append(t)
        return t

    return decorator


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def all_tools() -> List[BaseTool]:
    """Every registered tool, regardless of context."""
    return list(_TOOL_REGISTRY)


def tools_for(context: ExecutionContext) -> List[BaseTool]:
    """Every tool whose declared capability this context satisfies."""
    available = []
    for t in _TOOL_REGISTRY:
        if not isinstance(context, requires_of(t)):
            continue
        predicate = tool_meta(t).get("available_when")
        if predicate is not None and not predicate(context):
            continue
        available.append(t)
    return available


def resolve_toolsets(
    patterns: List[str], context: Optional[ExecutionContext] = None
) -> List[BaseTool]:
    """Expand toolset patterns into tools, optionally gated by a context.

    Patterns are fnmatch globs over dotted toolset names, so a role can request
    ``"universal"``, ``"tabular.spatial"``, or ``"*.profiling"`` and stay
    correct as new modalities register toolsets under those names.
    """
    candidates = tools_for(context) if context is not None else all_tools()
    return [
        t
        for t in candidates
        if any(fnmatch(toolset_of(t), pattern) for pattern in patterns)
    ]


def registered_toolsets() -> List[str]:
    """Sorted list of every toolset name currently registered."""
    return sorted({toolset_of(t) for t in _TOOL_REGISTRY})
