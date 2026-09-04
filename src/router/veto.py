"""Layer 4a — the deterministic veto: candidates that *cannot* answer a field.

Retrieval ranks by words, so its mistakes are word-shaped: "Fulton's condition
factor" wins a field asking for *temperature* because both say "condition", and
five taxonomy fields land on a ``pH`` treatment column because both say "level".
Measured on a labeled bundle, rank 1 answers 88% of a schema whose true answerable
rate is 24%.

Vocabulary cannot fix that — schema and data were written by different people, so
overlap is coincidence either way. What *can* is the evidence retrieval ignores:
the field's declared type, and the column's dtype, units, and actual values. A field
wanting a genus name is not answered by a column of integers; a field wanting whole
days is not answered by values running 0.89 to 1.22. Neither judgement needs a model,
and neither depends on what anything is *called*.

This runs **before** the field reader (:mod:`src.router.rerank`), so the reader
spends its calls on candidates that are at least dimensionally possible.

**A veto is permanent, so it must be conservative.** A vetoed candidate never reaches
the reader and can never be recovered, which makes a false veto a silent recall loss —
strictly worse than a false accept, which the reader still gets a chance to reject.
Every rule here therefore fires only on evidence both sides actually declared:
unknown types, missing units, tools, and document spans are all left alone.

The rules are narrower than they first look, and deliberately so. "A text field is
not answered by a numeric column" seems obviously true and is not: schemas routinely
declare a measured quantity as ``str`` to hold "12.5 °C" or a range, so that rule
vetoes a correct water-temperature match. What survives is the narrower claim it was
standing in for — a field asking for a *name* is not answered by a measurement — which
needs the field to say it is nominal, not merely to be typed as text.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from src.context.base_context import EvidenceRef
from src.router.catalog import ResolvedColumn
from src.router.schema import FieldSpec

# --- types ------------------------------------------------------------------

_OPTIONAL = re.compile(r"^Optional\[(.*)\]$")
_TEXT_TYPES = {"str", "string"}
#: Words marking a field as *nominal* — it wants something's name, not a measurement
#: of it. Kept to generic vocabulary: an entry here vetoes candidates in every
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
    reference to..." and matching that incidental word vetoes a correct candidate.
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
#: unit absent from here is unknown, and unknown never vetoes.
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
#: short: a wrong entry here vetoes a correct candidate for every bundle.
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


# --- the veto ---------------------------------------------------------------


def veto_reason(field: FieldSpec, column: ResolvedColumn) -> Optional[str]:
    """Why ``column`` cannot answer ``field``, or None if it is at least possible.

    Returns a sentence rather than a boolean so a rejection can be recorded and
    argued with, instead of a candidate silently vanishing from the set.
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


def apply_veto(
    field: FieldSpec, candidates: List[EvidenceRef], catalog: Any
) -> Tuple[List[EvidenceRef], List[str]]:
    """Split candidates into those that survive and the reasons the rest were cut.

    Tools and document spans always survive: neither declares a type or a unit, so
    there is nothing here to judge them on. Only resolved columns are testable, and
    only against what layer 3 actually established about them.
    """
    if catalog is None:
        return candidates, []
    kept: List[EvidenceRef] = []
    reasons: List[str] = []
    for candidate in candidates:
        if candidate.kind in ("tool", "quoted_span"):
            kept.append(candidate)
            continue
        column = catalog.find(candidate.locator, candidate.resource)
        reason = veto_reason(field, column) if column is not None else None
        if reason is None:
            kept.append(candidate)
        else:
            reasons.append(f"{candidate.resource}::{candidate.locator} — {reason}")
    return kept, reasons
