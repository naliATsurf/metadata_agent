"""Layer 1 — flatten a metadata standard's Pydantic schema to leaf fields.

The field-driven router starts from *what it must fill*: the target schema's
fields, each a natural retrieval query via its ``description``. But a schema is a
tree — nested models, optionals, unions — not a flat list, so a naive
``model_fields`` walk misses nested fields and mistakes an ``Optional`` for a
required one. :func:`walk_schema` flattens it to leaf :class:`FieldSpec`s with
dotted paths, unwrapping ``Optional``/``Union`` and recursing into nested models
while treating containers (``list``/``dict``) as leaves.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, List, Type, Union, get_args, get_origin

from pydantic import BaseModel


@dataclass(frozen=True)
class FieldSpec:
    """One leaf field the router must fill.

    ``description`` doubles as the retrieval query, so a schema with thin
    descriptions is a routing risk the walker surfaces (an empty string here
    means the field carries no query signal of its own).
    """

    path: str            # dotted path from the schema root, e.g. "spatial_coverage"
    description: str      # the field's description — the router's query
    type: str             # rendered leaf type name, e.g. "str", "Optional[Dict]"
    required: bool        # False for Optional / defaulted fields


def _is_model(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_optional) for ``Optional[X]`` / ``X | None``.

    A union with more than one non-``None`` member is left intact — it is a leaf
    the extractor must interpret, not a model to recurse into.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        is_optional = len(non_none) < len(args)
        if len(non_none) == 1:
            return non_none[0], is_optional
        return annotation, is_optional
    return annotation, False


def _type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is not None:
        return getattr(origin, "__name__", str(origin))
    return getattr(annotation, "__name__", str(annotation))


def walk_schema(model: Type[BaseModel], _prefix: str = "") -> List[FieldSpec]:
    """Flatten ``model`` to a list of leaf :class:`FieldSpec`s.

    Nested Pydantic models recurse with a dotted path; ``Optional``/``Union`` is
    unwrapped (and marks the field not-required); containers are leaves.
    """
    specs: List[FieldSpec] = []
    for name, info in model.model_fields.items():
        path = f"{_prefix}{name}"
        inner, is_optional = _unwrap_optional(info.annotation)
        required = info.is_required() and not is_optional

        if _is_model(inner):
            specs.extend(walk_schema(inner, _prefix=f"{path}."))
        else:
            type_name = _type_name(inner)
            if is_optional:
                type_name = f"Optional[{type_name}]"
            specs.append(
                FieldSpec(
                    path=path,
                    description=info.description or "",
                    type=type_name,
                    required=required,
                )
            )
    return specs
