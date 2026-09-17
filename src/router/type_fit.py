"""Layer 4a — type fit: does a candidate's type and unit fit the field? A grade, not a filter.

Retrieval ranks by words, so its mistakes are word-shaped: "Fulton's condition
factor" wins a field asking for *temperature* because both say "condition", and
taxonomy fields land on a ``pH`` treatment column because both say "level". What
words ignore, types and units see: a field wanting a genus name is not usually
answered by a column of integers, and a field wanting whole days not by values
running 0.89 to 1.22. Neither judgement needs a model.

**It lowers confidence; it never decides relevance.** This used to be a veto that
removed candidates before the judge saw them, and that was too strong for rules this
blunt. A type is a schema author's guess — ``str`` is what a schema reaches for when
unsure, a count may be declared ``float``, a unit may be written in a way this table
does not know — so a rule that is right most of the time still hides a correct column
some of the time, and a hidden column can never be recovered. Worse, removal made the
catalog differ per field, so fields could not share a column matcher's call.

So every candidate stays, and every judge sees the whole catalog with each column's
dtype, units and value range on its card. A mismatch is recorded on the routing and
caps its assurance at ``low``: a routing whose winner does not fit the field's type
is one to check, not one to trust.

The same grade covers a tool's arguments (:func:`argument_mismatch`): a date range
computed over a column of numbers is a routing to check.

**A column whose values vary is noted, not graded** (:func:`varies`). A column repeating
one value down every row *is* that value; one whose values differ must be summarised
first. Whether that is wrong depends on what the field wants, and a type cannot say:
"the temperature of the condition" answered by two temperatures is a question to ask,
but "minimum latitude" from a latitude column is exactly right. The column matcher
sees the distinct values on the card and can tell the two apart; this only records the
observation.

The rules are narrow anyway, because a false mismatch still costs confidence on a
correct answer. "A text field is not answered by a numeric column" seems obviously
true and is not: schemas routinely declare a measured quantity as ``str`` to hold
"12.5 °C" or a range. What survives is the narrower claim it was standing in for — a
field asking for a *name* is not answered by a measurement — which needs the field to
say it is nominal, not merely to be typed as text.
"""

from __future__ import annotations

import re
from typing import Any, Callable, List, Optional

from src.context.base_context import EvidenceRef
from src.router.catalog import ResolvedColumn
from src.router.schema import FieldSpec

# --- types ------------------------------------------------------------------

_OPTIONAL = re.compile(r"^Optional\[(.*)\]$")
_TEXT_TYPES = {"str", "string"}
#: Words marking a field as *nominal* — it wants something's name, not a measurement
#: of it. Kept to generic vocabulary: an entry here marks candidates down in every
#: bundle, so a domain word ("stage", "taxon") does not belong in it even when it
#: would help on one dataset.
_NOMINAL_WORDS = (
    "name", "label", "title", "identifier", "code", "category", "keyword",
    "acronym", "abbreviation",
)
_INT_TYPES = {"int", "integer"}
_FLOAT_TYPES = {"float", "decimal"}


def field_base_type(rendered: str) -> str:
    """The leaf type a rendered annotation asks for: str | int | float | other."""
    inner = _OPTIONAL.match((rendered or "").strip())
    name = (inner.group(1) if inner else rendered or "").strip().lower()
    if name in _TEXT_TYPES:
        return "str"
    if name in _INT_TYPES:
        return "int"
    if name in _FLOAT_TYPES:
        return "float"
    return "other"


def is_nominal(field: FieldSpec) -> bool:
    """Does the field ask for what something is *called*, rather than a quantity?

    Read from the field's **path only**, never its type or its description. The type
    says little — ``str`` is what a schema reaches for when unsure. The description
    says too much: ``trait_type`` is described as "...for trait *name* to make
    reference to..." and matching that incidental word marks a correct candidate down.
    A path is the field's own compact statement of what it is, and ``genus_name``
    means it in a way a sentence mentioning "name" does not.
    """
    # Split on the separators a dotted/underscored path uses: "_" is a *word*
    # character, so \bname\b never matches inside "genus_name" without this.
    words = set(re.split(r"[^a-z0-9]+", field.path.lower()))
    return bool(words & set(_NOMINAL_WORDS))


def _column_is_numeric(column: ResolvedColumn) -> bool:
    return column.dtype.startswith(("int", "float", "uint"))


def _column_is_text(column: ResolvedColumn) -> bool:
    return column.dtype.startswith(("object", "string", "category"))


# --- units ------------------------------------------------------------------

#: Unit tokens grouped by the physical dimension they measure. Only used to spot a
#: *mismatch* — two units in different groups cannot describe the same quantity. A
#: unit absent from here is unknown, and unknown is never a mismatch.
_DIMENSIONS = {
    "time": {"s", "sec", "secs", "second", "seconds", "min", "mins", "minute",
             "minutes", "h", "hr", "hrs", "hour", "hours", "d", "day", "days",
             "week", "weeks", "month", "months", "yr", "year", "years"},
    "temperature": {"c", "°c", "degc", "celsius", "k", "kelvin", "f", "°f",
                    "fahrenheit"},
    "mass": {"g", "kg", "mg", "µg", "ug", "ng", "gram", "grams", "kilogram",
             "kilograms", "lb", "lbs", "tonne", "tonnes"},
    "length": {"m", "cm", "mm", "km", "µm", "um", "nm", "metre", "metres", "meter",
               "meters", "centimetre", "centimetres", "centimeter", "centimeters",
               "inch", "inches", "ft"},
    "concentration": {"mg/l", "µg/l", "ug/l", "g/l", "ng/l", "ppm", "ppb", "mol/l",
                      "mmol/l", "µmol/l", "umol/l", "psu"},
    "proportion": {"%", "percent", "percentage"},
    "speed": {"m/s", "cm/s", "km/h", "cm s-1", "m s-1", "knots"},
}

#: Words in a *field description* that name the quantity it wants. Deliberately
#: short: a wrong entry here marks a correct candidate down in every bundle.
_QUANTITY_WORDS = {
    "duration": "time", "period": "time", "elapsed": "time",
    "temperature": "temperature",
    "mass": "mass", "weight": "mass", "biomass": "mass",
    "length": "length", "height": "length", "depth": "length", "distance": "length",
}

_UNIT_PHRASE = re.compile(
    r"\bin ([a-zµ°%/ -]{1,12}?)\b|\(([a-zµ°%/ -]{1,12}?)\)", re.I
)


def _dimension_of_unit(units: Optional[str]) -> Optional[str]:
    """Which dimension a declared unit string measures, if it is one we know."""
    if not units:
        return None
    token = units.strip().lower().replace("−", "-")
    for dimension, members in _DIMENSIONS.items():
        if token in members:
            return dimension
    return None


def _dimension_wanted(description: Optional[str]) -> Optional[str]:
    """Which dimension a field description asks for, when it says so plainly.

    Two ways a description states it: an explicit unit ("duration period **in
    days**", "temperature **(°C)**") or a quantity word ("the **mass** of the
    individual"). Anything less explicit is treated as unknown.
    """
    text = (description or "").lower()
    for match in _UNIT_PHRASE.finditer(text):
        candidate = (match.group(1) or match.group(2) or "").strip()
        dimension = _dimension_of_unit(candidate)
        if dimension:
            return dimension
    for word, dimension in _QUANTITY_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return dimension
    return None


# --- the grade --------------------------------------------------------------


def mismatch(field: FieldSpec, column: ResolvedColumn) -> Optional[str]:
    """Why ``column``'s type or units do not fit ``field``, or None if they may.

    A sentence rather than a boolean, so a routing records the reason its confidence
    was lowered and a reader can argue with it.
    """
    wanted = field_base_type(field.type)

    if wanted == "str" and is_nominal(field) and _column_is_numeric(column):
        return (
            f"field asks for a name; {column.name} holds numbers ({column.dtype})"
        )
    if wanted in ("int", "float") and _column_is_text(column):
        return f"field asks for a number; {column.name} holds text ({column.dtype})"
    if wanted == "int" and _column_is_numeric(column) and column.value_integral is False:
        low, high = column.value_range or (0.0, 0.0)
        return (
            f"field asks for a whole number; {column.name} holds fractional values "
            f"({low:g} to {high:g})"
        )

    wants = _dimension_wanted(field.description)
    has = _dimension_of_unit(column.units)
    if wants and has and wants != has:
        return (
            f"field asks for a {wants} quantity; {column.name} is measured in "
            f"{column.units} ({has})"
        )
    return None


def varies(field: FieldSpec, column: ResolvedColumn) -> Optional[str]:
    """A note when a field typed as one value is routed to a column whose values differ."""
    if field_base_type(field.type) == "other" or (column.distinct_count or 0) <= 1:
        return None
    return f"{column.name} holds {column.distinct_count} different values"


def argument_mismatch(argument: str, values: str, column: ResolvedColumn) -> Optional[str]:
    """Why ``column`` does not fit a tool argument wanting ``values``, or None if it may.

    ``values`` is the argument's declared kind (:class:`~src.tools.base.ColumnArg`):
    ``temporal`` wants dates or times, ``numeric`` numbers, ``any`` anything.
    """
    if values == "temporal" and column.value_label != "temporal":
        return f"{argument} needs dates or times; {column.name} holds {column.dtype} values"
    if values == "numeric" and not _column_is_numeric(column):
        return f"{argument} needs numbers; {column.name} holds {column.dtype} values"
    return None


def mismatches(
    field: FieldSpec, candidates: List[EvidenceRef], catalog: Any
) -> List[str]:
    """The type and unit mismatches among ``candidates``, as ``ref — reason`` lines.

    Tools and document spans are never graded: neither declares a type or a unit.
    Only resolved columns are, and only on what layer 3 established about them.
    """
    return _per_column(field, candidates, catalog, mismatch)


def variations(
    field: FieldSpec, candidates: List[EvidenceRef], catalog: Any
) -> List[str]:
    """The :func:`varies` notes among ``candidates``, as ``ref — note`` lines."""
    return _per_column(field, candidates, catalog, varies)


def _per_column(
    field: FieldSpec,
    candidates: List[EvidenceRef],
    catalog: Any,
    check: Callable[[FieldSpec, ResolvedColumn], Optional[str]],
) -> List[str]:
    if catalog is None:
        return []
    found: List[str] = []
    for candidate in candidates:
        if candidate.kind in ("tool", "quoted_span"):
            continue
        column = catalog.find(candidate.locator, candidate.resource)
        reason = check(field, column) if column is not None else None
        if reason is not None:
            found.append(f"{candidate.resource}::{candidate.locator} — {reason}")
    return found
