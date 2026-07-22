"""Layer 3 — catalog resolution (symbol linking).

The schema router matches a field's query against a table's *column catalog*. Real
datasets defeat that: a latitude column named ``la``, a random code. No embedding
turns ``la`` into "latitude" — the signal is not there. But it usually *is*
elsewhere in the bundle: a codebook row ``la | Latitude | decimal degrees``, or a
README line. So this is a **missing-context problem**, and the fix is to import
the missing context into the catalog *before* routing.

:func:`resolve_catalog` turns each opaque column into a *described* column by
harvesting explanations from the other resources, escalating most-authoritative
first and stopping at the first strong hit:

1. **structured dictionary** — a data-dictionary table keyed by column name;
2. **lexical prose** — a definition like ``la = latitude`` in a document;
3. **self-evident value type** — the *only* thing values can identify on their
   own: a coordinate range, a parseable date. Not a general identifier.

**The value profile is a referee, not a guesser.** Values genuinely identify only
a few kinds (coordinates, dates); for the long tail — pH, biomass, a trait score —
"numeric, [0.3, 8.7]" names nothing, so an undescribed column of that kind
*abstains* (``link_method="none"``) rather than borrow a meaningless label.
"Unresolved" is a first-class, honest outcome. What the profile *is* reliable for
is **refutation**: a codebook that says ``tmp`` is Kelvin while the values are
4–22 is flagged — the profile need not know what ``tmp`` is to know it is not
Kelvin. That is the two-hop grounding (the *value* is computed; the
*interpretation* is a cited claim the values then discipline).

Scale note: this is **doc-scale, not row-scale**. Value profiles are computed from
a *sample* of the data (never a full scan), and only the small description sources
are read in whole. A million-row data table is sampled, never indexed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from src.context.base_context import (
    EvidenceRef,
    ExecutionContext,
    Searchable,
    TabularContext,
    bm25_scores,
    content_terms,
    tokenize,
)
from src.context.text_context import TextContext
from src.tools.tabular.detection import (
    detect_coordinate_values,
    detect_temporal_dtype,
)

# How much of the target's columns a candidate key column must cover for a
# tabular resource to count as a data dictionary for it.
_DICTIONARY_COVERAGE = 0.5
# Rows sampled to compute a value profile. We sample, never scan the whole table:
# approximate stats are enough for a prior and for the refutation cross-check, and
# this keeps resolution doc-scale — cost follows the schema and the docs, not the
# row count.
_PROFILE_SAMPLE = 1000

_DESC_NAME_RE = re.compile(r"desc|label|meaning|definition|name|title", re.I)
_UNIT_NAME_RE = re.compile(r"unit", re.I)
_NOTE_NAME_RE = re.compile(r"note|comment|remark", re.I)


@dataclass(frozen=True)
class ResolvedColumn:
    """One column after symbol linking: what it means, and on what evidence."""

    resource: str
    name: str
    dtype: str
    description: Optional[str] = None       # resolved human meaning
    units: Optional[str] = None
    link_method: str = "none"              # structured_dictionary | lexical_prose | value_prior | none
    link_confidence: str = "none"          # high | medium | low | none
    link_evidence: Optional[str] = None    # citation for the interpretation
    value_label: Optional[str] = None      # coarse prior: coordinate | temporal | numeric | categorical
    conflicts: List[str] = field(default_factory=list)

    def document(self) -> str:
        """The text the enriched catalog search ranks this column on."""
        parts = [self.name, self.description or "", self.units or "", self.value_label or ""]
        return " ".join(p for p in parts if p)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "name": self.name,
            "dtype": self.dtype,
            "description": self.description,
            "units": self.units,
            "link_method": self.link_method,
            "link_confidence": self.link_confidence,
            "link_evidence": self.link_evidence,
            "value_label": self.value_label,
            "conflicts": self.conflicts,
        }


@dataclass
class Catalog:
    """The enriched column catalog the router routes over."""

    resource: str
    columns: List[ResolvedColumn]

    def get(self, name: str) -> Optional[ResolvedColumn]:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def conflicts(self) -> List[str]:
        return [f"{c.name}: {msg}" for c in self.columns for msg in c.conflicts]

    def search(self, query: str, k: int = 5) -> List[EvidenceRef]:
        """Rank the *resolved* columns against the query.

        Same BM25 ranking as :meth:`TabularContext.search`, but over the enriched
        documents — so a query for "latitude" now reaches column ``la`` through
        its resolved description, closing the semantic gap.
        """
        query_terms = content_terms(query)
        docs = [tokenize(c.document()) for c in self.columns]
        scores = bm25_scores(query_terms, docs)
        refs = [
            EvidenceRef(
                resource=c.resource,
                locator=c.name,
                kind="computed_column",
                snippet=f"{c.name}: {c.description or c.value_label or c.dtype}"
                + (f" [{c.link_confidence}]" if c.link_method != "none" else ""),
                score=score,
            )
            for c, score in zip(self.columns, scores)
            if score > 0
        ]
        refs.sort(key=lambda r: r.score, reverse=True)
        return refs[:k]

    def to_dict(self) -> Dict[str, Any]:
        return {"resource": self.resource, "columns": [c.to_dict() for c in self.columns]}


# ---------------------------------------------------------------------------
# Structured-dictionary linking (highest precision)
# ---------------------------------------------------------------------------


@dataclass
class _Dictionary:
    """A parsed data dictionary: column name -> its harvested description."""

    resource: str
    by_name: Dict[str, Dict[str, Optional[str]]]


def _pick_column(df: pd.DataFrame, pattern: re.Pattern, exclude: str) -> Optional[str]:
    for col in df.columns:
        if col != exclude and pattern.search(str(col)):
            return col
    return None


def _as_dictionary(src: TabularContext, target_columns: List[str]) -> Optional[_Dictionary]:
    """Recognise ``src`` as a data dictionary for ``target_columns``, if it is one.

    A tabular resource is a dictionary when one of its columns' values covers most
    of the target's column names (the *key* column). The best-matching description,
    units, and notes columns supply the payload.
    """
    target_set = {c.lower() for c in target_columns}
    if not target_set:
        return None

    for resource in src.resources:
        df = src.read_resource(resource)
        best_key, best_cover = None, 0.0
        for col in df.columns:
            values = {str(v).strip().lower() for v in df[col].dropna()}
            cover = len(target_set & values) / len(target_set)
            if cover > best_cover:
                best_key, best_cover = col, cover

        # A codebook is column-scale: one row per column, a key column whose
        # values ARE the schema's names. A row-scale *data* table holds
        # observations, so no column of it covers the schema names — coverage ≈ 0.
        # This test is what keeps a huge data table from being read as a codebook.
        if best_key is None or best_cover < _DICTIONARY_COVERAGE:
            continue

        desc_col = _pick_column(df, _DESC_NAME_RE, best_key)
        unit_col = _pick_column(df, _UNIT_NAME_RE, best_key)
        note_col = _pick_column(df, _NOTE_NAME_RE, best_key)

        by_name: Dict[str, Dict[str, Optional[str]]] = {}
        for _, row in df.iterrows():
            key = str(row[best_key]).strip()
            if not key:
                continue
            by_name[key.lower()] = {
                "description": _cell(row, desc_col),
                "units": _cell(row, unit_col),
                "notes": _cell(row, note_col),
                "key": key,
            }
        return _Dictionary(resource=resource, by_name=by_name)
    return None


def _cell(row: pd.Series, col: Optional[str]) -> Optional[str]:
    if col is None:
        return None
    val = row[col]
    if pd.isna(val):
        return None
    text = str(val).strip()
    return text or None


# ---------------------------------------------------------------------------
# Lexical prose linking (a document *defines* the token)
# ---------------------------------------------------------------------------


def _prose_definition(name: str, docs: List[TextContext]) -> Optional[Dict[str, Any]]:
    """Find a definition like ``la = latitude`` / ``la: latitude`` in the prose."""
    # token, optionally backticked/quoted, then a separator, then a short phrase.
    pattern = re.compile(
        rf"[`'\"]?{re.escape(name)}[`'\"]?\s*[:=—-]\s*([A-Za-z][A-Za-z0-9 /()%-]{{2,60}})"
    )
    for doc in docs:
        for resource in doc.resources:
            text = doc.read_text(resource)
            match = pattern.search(text)
            if match:
                phrase = match.group(1).strip().rstrip(".;,")
                return {
                    "description": phrase,
                    "evidence": f"{resource}#{match.start()}",
                }
    return None


# ---------------------------------------------------------------------------
# Value priors (the recomputable floor) and cross-check
# ---------------------------------------------------------------------------


def _value_profile(series: pd.Series) -> Dict[str, Any]:
    """A light, recomputable profile of a column's values."""
    profile: Dict[str, Any] = {"numeric": False, "min": None, "max": None, "label": None}
    if detect_temporal_dtype(series):
        profile["label"] = "temporal"
        return profile
    if pd.api.types.is_numeric_dtype(series):
        sample = series.dropna()
        if len(sample):
            profile["numeric"] = True
            profile["min"] = float(sample.min())
            profile["max"] = float(sample.max())
        # Coordinates are continuous floats — restrict the prior to float columns so
        # integer counts/ids (which also fall in [-90, 90]) are not mislabelled.
        # Even so, a single column cannot tell latitude from longitude, nor a
        # coordinate from any other small float; hence "medium" confidence and a
        # description that says so. The dictionary link, when present, overrides it.
        coord = pd.api.types.is_float_dtype(series) and detect_coordinate_values(series)
        profile["label"] = "coordinate" if coord else "numeric"
        return profile
    nunique = series.dropna().nunique()
    if 0 < nunique <= max(20, len(series) // 10):
        profile["label"] = "categorical"
    return profile


# The *only* value profiles that identify a routing-useful kind on their own,
# each as (description, confidence). Temporal parses reliably (high); a coordinate
# range is recomputable but ambiguous — it cannot tell latitude from longitude,
# nor a coordinate from any other small float (medium). Everything else — a
# categorical code, a generic numeric measure — is the long tail the values
# cannot name, so it is deliberately absent here: those columns abstain.
_SELF_EVIDENT = {
    "coordinate": ("geographic coordinate (latitude or longitude by value range)", "medium"),
    "temporal": ("date or time value", "high"),
}


def _cross_check(description: Optional[str], units: Optional[str], profile: Dict[str, Any]) -> List[str]:
    """Flag a claimed meaning/units that the values contradict."""
    if not profile.get("numeric"):
        return []
    lo, hi = profile["min"], profile["max"]
    text = f"{description or ''} {units or ''}".lower()
    conflicts: List[str] = []
    if "kelvin" in text and hi < 150:
        conflicts.append(f"claimed units Kelvin, but values {lo:g}–{hi:g} lie in the Celsius range")
    if "latitude" in text and (lo < -90 or hi > 90):
        conflicts.append(f"claimed latitude, but values {lo:g}–{hi:g} fall outside [-90, 90]")
    if "longitude" in text and (lo < -180 or hi > 180):
        conflicts.append(f"claimed longitude, but values {lo:g}–{hi:g} fall outside [-180, 180]")
    if ("percent" in text or (units or "").strip() == "%") and (lo < 0 or hi > 100):
        conflicts.append(f"claimed percentage, but values {lo:g}–{hi:g} fall outside [0, 100]")
    return conflicts


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_catalog(
    target: TabularContext,
    resource: str = "",
    sources: Optional[List[ExecutionContext]] = None,
) -> Catalog:
    """Enrich ``target``'s columns with meanings harvested from ``sources``.

    ``sources`` are the other resources in the bundle — data dictionaries and
    description documents — each auto-classified: a tabular source that keys the
    target's columns is parsed as a dictionary; text sources are searched for
    prose definitions. Value priors, computed from the target's own values, are
    the floor when neither yields a link, and the basis for cross-checking any
    link that does.
    """
    resource = resource or target.resources[0]
    info = target.get_resource_info(resource)
    frame = target.read_resource(resource, limit=_PROFILE_SAMPLE)
    sources = sources or []

    dictionaries = [
        d
        for src in sources
        if isinstance(src, TabularContext)
        for d in [_as_dictionary(src, info.field_names)]
        if d is not None
    ]
    docs = [src for src in sources if isinstance(src, TextContext)]

    resolved: List[ResolvedColumn] = []
    for f in info.fields:
        profile = _value_profile(frame[f.name]) if f.name in frame.columns else {"numeric": False}
        resolved.append(
            _resolve_column(f.name, f.dtype, resource, profile, dictionaries, docs)
        )
    return Catalog(resource=resource, columns=resolved)


def _resolve_column(name, dtype, resource, profile, dictionaries, docs) -> ResolvedColumn:
    label = profile.get("label")

    # 1. structured dictionary
    for d in dictionaries:
        entry = d.by_name.get(name.lower())
        if entry and (entry["description"] or entry["units"]):
            return ResolvedColumn(
                resource=resource, name=name, dtype=dtype,
                description=entry["description"], units=entry["units"],
                link_method="structured_dictionary", link_confidence="high",
                link_evidence=f"{d.resource} row '{entry['key']}'",
                value_label=label,
                conflicts=_cross_check(entry["description"], entry["units"], profile),
            )

    # 2. lexical prose definition
    prose = _prose_definition(name, docs)
    if prose:
        return ResolvedColumn(
            resource=resource, name=name, dtype=dtype,
            description=prose["description"], units=None,
            link_method="lexical_prose", link_confidence="medium",
            link_evidence=prose["evidence"], value_label=label,
            conflicts=_cross_check(prose["description"], None, profile),
        )

    # 3. self-evident value type — the only identification the values can support
    #    on their own (a coordinate range, a parseable date). A referee, not a
    #    guesser: it never fires for the long tail.
    if label in _SELF_EVIDENT:
        description, confidence = _SELF_EVIDENT[label]
        return ResolvedColumn(
            resource=resource, name=name, dtype=dtype,
            description=description, link_method="value_prior",
            link_confidence=confidence,
            link_evidence=f"value profile of '{name}' ({dtype})",
            value_label=label,
        )

    # 4. abstain — nothing describes this column and its values cannot name it.
    #    "Unresolved" is a first-class outcome; a fabricated label is worse. The
    #    value_label is still carried for metadata and for the cross-check.
    return ResolvedColumn(resource=resource, name=name, dtype=dtype, value_label=label)
