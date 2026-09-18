"""Layer 4b, columns — match schema fields, and tools' arguments, against the catalog.

Choosing a column is a *matching* problem, not a retrieval one. The catalog is fixed
and identical for every field, so ranking it once per field and judging each field's
top few repeats most of the catalog in every call while hiding the rest: a field is
only ever compared with the columns that happened to share its words. Here the
catalog is shown whole, once, and every field is matched against it.

Two things keep that affordable as a catalog grows, the second used only when the
first is not enough:

1. **Columns that mean the same thing are shown once** (:func:`merge_columns`). Real
   bundles repeat themselves: ``pH``, ``tank`` and ``ID`` sit in every table of
   ``TRADAT031``, resolved by layer 3 to the same meaning each time. One card stands
   for all of them and a match fans back out to every table, which is also how a
   field answered by six tables gets all six.
2. **A catalog too large for one call is split** (:func:`split_cards`) into slices
   under ``router_match_max_chars`` (:mod:`src.thresholds`), each sent with the full field list — the short part is
   repeated, the long part is not. A field can match in more than one slice, and
   the strongest verdict wins.

Every field sees the same catalog. Nothing is withheld on type or units — each card
shows its column's dtype, units, value range and, when there are few, its distinct
values — and the router grades a pick that does not fit (:mod:`src.router.type_fit`)
rather than hiding the column beforehand.

**Tools are not on the catalog.** Whether a field is computed is the tool matcher's
question (:mod:`src.router.tool_matcher`). What reaches this matcher is the other half:
for each tool chosen, one line per argument — ``sample_size[resource]`` asking for a
table, ``measure_date[time_column]`` for a column — answered from the same cards, with
a card per table added when a table is asked for.

Matching needs no quote. A column's card *is* its evidence — meaning, units, value
range — and what a model would copy back from it is the codebook text layer 3
already cited. The referee checks what can be checked: the pick names a card that
was actually shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src import thresholds
from src.router.catalog import ResolvedColumn
from src.router.judge import (
    TABLE_PREFIX,
    Verdict,
    ask,
    dispatch,
    field_lines,
    in_groups,
    pick,
    rank_of,
)
from src.router.schema import FieldSpec


def column_ref(column: ResolvedColumn) -> str:
    """The ref a column goes by — the label vocabulary's ``table::column``."""
    return f"{column.resource}::{column.name}"


@dataclass(frozen=True)
class ColumnGroup:
    """Columns that mean the same thing, shown to the matcher as one card.

    The group's ref is its first member's, so a match still names a real
    ``table::column`` — the vocabulary a hand label is written in.
    """

    members: Tuple[ResolvedColumn, ...]

    @property
    def ref(self) -> str:
        return column_ref(self.members[0])


def _normalized(text: Optional[str]) -> str:
    return " ".join((text or "").casefold().split())


def merge_columns(columns: Sequence[ResolvedColumn]) -> List[ColumnGroup]:
    """Group columns layer 3 resolved to the same meaning and units, in catalog order.

    Only a *described* column merges: two columns with no resolved meaning share
    nothing but their silence, and merging them would claim an equivalence nobody
    established. Agreement is taken at its word — identical meaning and units after
    case and spacing — because layer 3's claim comparer has already done the semantic
    work of settling each column's description; this only notices when two tables
    ended up with the same one.
    """
    groups: Dict[Any, List[ResolvedColumn]] = {}
    for column in columns:
        meaning = _normalized(column.description)
        key: Any = (meaning, _normalized(column.units)) if meaning else column_ref(column)
        groups.setdefault(key, []).append(column)
    return [ColumnGroup(tuple(members)) for members in groups.values()]


def group_card(
    group: ColumnGroup, members: Optional[Sequence[ResolvedColumn]] = None
) -> Dict[str, Any]:
    """Everything known about a group, as the flat card the matcher rules on.

    ``members`` narrows the card to a subset of the group's columns. Units and value
    range are the load-bearing parts:
    reading ``EPOC::Duration`` (recovery minutes) as a condition's duration in days,
    or ``growth::condition`` (unitless, 0.89–1.22) as a temperature, is decidable from
    those and from nothing else.
    """
    shown = list(members if members is not None else group.members)
    first = shown[0]
    tables = sorted({c.resource for c in shown})
    names = sorted({c.name.strip() for c in shown})
    card: Dict[str, Any] = {
        "ref": group.ref,
        "kind": "column",
        "table": tables[0] if len(tables) == 1 else tables,
        "column": names[0] if len(names) == 1 else names,
        "meaning": first.description,
        "units": first.units,
        "dtype": first.dtype,
        "value_prior": first.value_label,
        "meaning_cited_from": first.link_evidence,
        "source_text": first.link_quote,
    }
    ranges = [c.value_range for c in shown if c.value_range]
    if ranges:
        low = min(r[0] for r in ranges)
        high = max(r[1] for r in ranges)
        card["value_range"] = f"{low:g} to {high:g}"
    card.update(_distinct(shown))
    return {key: value for key, value in card.items() if value not in (None, "", [])}


def _distinct(columns: Sequence[ResolvedColumn]) -> Dict[str, Any]:
    """The values a group takes when they are few, else how many there are.

    A column holding one value down every row *is* that value for the dataset, which
    the card can only say by listing it. Across merged columns the lists are joined;
    where any member has too many to list, the count is the largest member's.
    """
    listed = [c.distinct_values for c in columns]
    if all(values is not None for values in listed):
        joined = list(dict.fromkeys(value for values in listed for value in values))
        if len(joined) <= thresholds.current().catalog_distinct_values_max:
            return {"distinct_values": joined}
        return {"distinct_count": len(joined)}
    counts = [c.distinct_count for c in columns if c.distinct_count is not None]
    return {"distinct_count": max(counts)} if counts else {}


def table_card(table: str, columns: Sequence[ResolvedColumn]) -> Dict[str, Any]:
    """A whole table, offered for a tool's ``resource`` argument: its name and columns."""
    return {
        "ref": f"{TABLE_PREFIX}{table}",
        "kind": "table",
        "columns": [c.name.strip() for c in columns],
    }


def split_cards(cards: Sequence[Dict[str, Any]], max_chars: int) -> List[List[Dict[str, Any]]]:
    """Pack column cards, in order, into slices under ``max_chars``; tables join every slice.

    A table card is only there to be chosen as a whole, and there are only ever a few,
    so each slice carries all of them. A single card over the budget is a slice of its
    own — a card is never cut.
    """
    tables = [c for c in cards if c.get("kind") == "table"]
    columns = [c for c in cards if c.get("kind") != "table"]
    slices: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    size = 0
    for card in columns:
        width = len(json.dumps(card, default=str))
        if current and size + width > max_chars:
            slices.append(current)
            current, size = [], 0
        current.append(card)
        size += width
    if current:
        slices.append(current)
    return [tables + part for part in slices] or [tables]


#: One set of fields and the catalog cards they are matched against.
Request = Tuple[Sequence[FieldSpec], Sequence[Dict[str, Any]]]


class ColumnMatcher:
    """The seam: match fields to the column that holds each, or to none.

    Implementations receive fields and cards and nothing else, so a matcher never
    touches a catalog, a context, or an SDK. :meth:`match` is one judgement over one
    catalog; :meth:`match_many` is what the router calls, one request per set of
    fields sharing a catalog. Fields never repeat across requests.
    """

    def match(
        self, *, fields: Sequence[FieldSpec], cards: Sequence[Dict[str, Any]]
    ) -> Dict[str, Verdict]:
        raise NotImplementedError

    def match_many(self, *, requests: Sequence[Request]) -> Dict[str, Verdict]:
        """Match every request, keyed by field path. Default: loop :meth:`match`."""
        verdicts: Dict[str, Verdict] = {}
        for fields, cards in requests:
            verdicts.update(self.match(fields=fields, cards=cards))
        return verdicts


PROMPT = (
    "You match metadata fields to the data columns that hold them.\n\n"
    "A column answers a field only if it holds *the quantity the field asks for*. "
    "Sharing a word is not enough. Check the units, the value range and the distinct "
    "values: a field wanting a duration in days is not answered by a column of "
    "minutes, and a field wanting a temperature is not answered by a unitless index "
    "ranging 0.9 to 1.2. A column measuring the subject's response is not the "
    "experimental condition it was measured under, and an identifier or a treatment "
    "label is not a measurement. A column whose distinct_values is a single value "
    "holds that value for every record. When a field asks for one value and the column "
    "holds several, lower your confidence — unless the field asks for a summary of "
    "them, such as a range, a minimum or a list.\n\n"
    "Most fields in a typical dataset have NO answer, because the schema and the data "
    "were written by different people for different purposes. Answering with null is "
    "the normal, expected outcome — a wrong source is far worse than none. Judge each "
    "field on its own: one column may answer several fields, and most columns answer "
    "none.\n\n"
    "A line written field[argument] is not a field. It asks what a tool should be run "
    "on to compute that field: a table card's ref when the line says (table), a column "
    "card's ref when it says (column), or null when no card holds what the line "
    "describes. Judge it by what the argument must hold — the column does not have to "
    "state the field's value itself.\n\n"
    'Return ONE JSON object mapping each line\'s name, as written, to {{"choice": <the '
    'ref of the card that answers it, or null>, "because": <why, naming the units or '
    'values that decide it>, "confidence": "high"|"medium"|"low"}}. No prose outside '
    "the JSON, no code fence.\n\n"
    # The catalog precedes the fields so that calls over one catalog share a prefix,
    # which an endpoint with prefix caching processes once.
    "CATALOG:\n{cards}\n\n"
    "FIELDS:\n{fields}\n"
)


class LLMColumnMatcher(ColumnMatcher):
    """LLM-backed matcher — abstention first-class.

    ``invoke`` is the only dependency: a callable ``prompt -> model text``, which keeps
    this free of any SDK and testable with a stub.

    Each request is split into calls along both axes: the catalog into slices under
    ``max_chars`` (:func:`split_cards`), and the fields into even groups of at most
    ``max_fields`` — or one field per call with ``batch=False``, the comparison worth
    running against a labeled sheet, since fields matched together are not
    independent. ``max_workers`` issues the calls concurrently.
    """

    def __init__(
        self,
        invoke: Callable[[str], str],
        *,
        batch: bool = True,
        max_workers: int = 1,
        max_chars: Optional[int] = None,
        max_fields: Optional[int] = None,
    ) -> None:
        self._invoke = invoke
        self._batch = batch
        self._max_workers = max(1, max_workers)
        self._max_chars = max_chars
        self._max_fields = max_fields
        self._cache: Dict[str, dict] = {}

    def match(
        self, *, fields: Sequence[FieldSpec], cards: Sequence[Dict[str, Any]]
    ) -> Dict[str, Verdict]:
        """One call: these fields against exactly these cards."""
        if not fields:
            return {}
        if not cards:
            return {f.path: Verdict(choice=None, because="no catalog to match against")
                    for f in fields}
        prompt = PROMPT.format(
            cards=json.dumps(list(cards), indent=2, default=str),
            fields=field_lines(fields),
        )
        if prompt not in self._cache:
            self._cache[prompt] = ask(self._invoke, prompt)
        data = self._cache[prompt]
        return {f.path: _referee(data.get(f.path), cards) for f in fields}

    def match_many(self, *, requests: Sequence[Request]) -> Dict[str, Verdict]:
        # Unset limits are read when the call is made, so a threshold override reaches
        # a matcher built before it.
        limits = thresholds.current()
        max_chars = self._max_chars or limits.router_match_max_chars
        per_call = (self._max_fields or limits.router_max_fields_per_call) if self._batch else 1
        calls = [
            (group, part)
            for fields, cards in requests
            for part in split_cards(cards, max_chars)
            for group in in_groups(list(fields), per_call)
        ]
        results = dispatch(
            lambda call: self.match(fields=call[0], cards=call[1]),
            calls, self._max_workers,
        )
        # A field meets several slices; the strongest verdict from any of them stands,
        # and an earlier slice wins a tie — the catalog's own order.
        verdicts: Dict[str, Verdict] = {}
        for result in results:
            for path, verdict in result.items():
                held = verdicts.get(path)
                if held is None or _stronger(verdict, held):
                    verdicts[path] = verdict
        return verdicts


def _stronger(new: Verdict, held: Verdict) -> bool:
    """Does ``new`` beat ``held``? A pick beats an abstention; then confidence decides."""
    if new.abstained:
        return False
    return held.abstained or rank_of(new.confidence) > rank_of(held.confidence)


def _referee(data: Any, cards: Sequence[Dict[str, Any]]) -> Verdict:
    """Validate one line's answer against the cards that call actually showed.

    Whether a table was picked for a field, or a column for a table, is the router's
    to refuse: it knows which lines are arguments.
    """
    return pick(data, {card["ref"] for card in cards}, "column matcher")
