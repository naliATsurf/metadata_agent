"""Layer 3 — catalog resolution (symbol linking).

The schema router matches a field's query against a table's *column catalog*. Real
datasets defeat that: a latitude column named ``la``, a random code. No embedding
turns ``la`` into "latitude" — the signal is not there. But it usually *is*
elsewhere in the bundle: a codebook row ``la | Latitude | decimal degrees``, or a
README line. So this is a **missing-context problem**, and the fix is to import
the missing context into the catalog *before* routing.

:func:`resolve_catalog` turns each opaque column into a *described* column by
harvesting explanations from the other resources. It does **not** stop at the
first hit: it gathers *every* candidate resolution for a column — from every
dictionary, every prose definition, and the value prior — then chooses among them
by assurance tier, with the value profile as referee for conflicts:

1. **structured dictionary** — a data-dictionary table keyed by column name;
2. **lexical prose** — a definition like ``la = latitude`` in a document;
3. **self-evident value type** — the *only* thing values can identify on their
   own: a coordinate range, a parseable date. Not a general identifier.

Sources that **agree** raise confidence and are recorded in ``corroborated_by``
(the citations that confirm the resolution — the positive counterpart of a
conflict); sources that **disagree** are surfaced in ``conflicts`` with the losing
candidates kept in ``alternatives``; nothing is decided by list order. Semantic
reconciliation of differing free-text descriptions needs an LLM and is deferred;
deterministically this adjudicates units, value-refutable claims, and verbatim
agreement.

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

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
from src.context.text_context import TextChunk, TextContext
from src.tools.tabular.detection import (
    detect_coordinate_values,
    detect_temporal_dtype,
)

# A source is a data dictionary when one of its columns keys the schema's columns:
# its values are *mostly* schema names (precision) and *unique* (a key has one row
# per column). Precision, not recall, lets a partial codebook through on the rows
# it covers rather than discarding it wholesale; uniqueness rejects a row-scale
# data table whose cells are repeated observations (a constant or high-cardinality
# column can't masquerade as a key). Together they need no coverage threshold.
_DICTIONARY_KEY_PRECISION = 0.5
_DICTIONARY_KEY_UNIQUENESS = 0.9
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
    corroborated_by: List[str] = field(default_factory=list)  # citations of agreeing sources
    alternatives: List[Dict[str, Any]] = field(default_factory=list)  # candidates that lost

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
            "corroborated_by": self.corroborated_by,
            "alternatives": self.alternatives,
        }


@dataclass
class Catalog:
    """The enriched column catalog the router routes over."""

    resource: str
    columns: List[ResolvedColumn]

    def get(self, name: str) -> Optional[ResolvedColumn]:
        return next((c for c in self.columns if c.name == name), None)

    def find(self, name: str, resource: Optional[str] = None) -> Optional[ResolvedColumn]:
        """Look a column up by name, optionally disambiguated by resource.

        With a multi-table catalog the same column name can occur in two tables, so
        matching on name alone (:meth:`get`) is ambiguous. Callers that hold the
        resource (a routed candidate does) should pass it.
        """
        return next(
            (
                c
                for c in self.columns
                if c.name == name and (resource is None or c.resource == resource)
            ),
            None,
        )

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
        # The key column is the one whose values best *are* the schema's column
        # names — ranked by how many it matches, then how pure it is.
        best_key, best_matches, best_precision, best_uniqueness = None, 0, 0.0, 0.0
        for col in df.columns:
            nonnull = df[col].dropna()
            values = {str(v).strip().lower() for v in nonnull}
            matches = len(target_set & values)
            if matches == 0 or len(nonnull) == 0:
                continue
            precision = matches / len(values)          # how much of the column is names
            uniqueness = len(values) / len(nonnull)    # a key has one row per value
            if (matches, precision) > (best_matches, best_precision):
                best_key = col
                best_matches, best_precision, best_uniqueness = matches, precision, uniqueness

        # Accept only a key column that is mostly schema names and (near-)unique.
        # Precision lets a partial codebook through; uniqueness keeps a row-scale
        # data table — repeated observations — from being read as a codebook.
        if (
            best_key is None
            or best_precision < _DICTIONARY_KEY_PRECISION
            or best_uniqueness < _DICTIONARY_KEY_UNIQUENESS
        ):
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


def _prose_candidates(name: str, docs: List[TextContext]) -> List[Dict[str, Any]]:
    """Find glossary-style definitions (``la = latitude``, ``pH – acclimation pH``).

    Returns one candidate per document that defines the token — so a definition
    appearing in several docs contributes several candidates, which the decision
    step then treats as corroboration or conflict.

    The separator is either ``:`` / ``=`` (whitespace optional) or a dash — hyphen,
    en-dash, or em-dash — that must be **surrounded by whitespace**. Real glossaries
    use the en-dash (``pH – acclimation pH``); requiring spaces around a dash keeps a
    hyphen *inside* a compound word from matching (``tank-level`` is not a definition
    of ``tank``). Matching is **case-insensitive** (a ``mass`` column finds
    ``Mass – fish mass (g)``); a lookbehind keeps the token from matching inside a
    longer word (``id`` does not match ``individual``).
    """
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])[`'\"]?{re.escape(name)}[`'\"]?(?:\s*[:=]\s*|\s+[–—-]\s+)"
        rf"([A-Za-z][A-Za-z0-9 /()%-]{{2,60}})",
        re.IGNORECASE,
    )
    found: List[Dict[str, Any]] = []
    for doc in docs:
        for resource in doc.resources:
            match = pattern.search(doc.read_text(resource))
            if match:
                found.append({
                    "description": match.group(1).strip().rstrip(".;,"),
                    "evidence": f"{resource}#{match.start()}",
                })
    return found


# ---------------------------------------------------------------------------
# Prose *reading* (retrieve-then-read) — the fallback above the glossary regex
# ---------------------------------------------------------------------------
#
# The glossary regex (`_prose_candidates`) scans a whole document for one fixed
# shape (`term <sep> definition`). That fits a short codebook README and nothing
# else: a long manuscript defines a column narratively, scattered across pages and
# files, and `search`-ing the whole text for the first match yields an arbitrary,
# usually wrong, hit. This tier fixes both halves of that:
#
#   1. **Retrieve** — BM25-rank *every chunk across every document* by the column
#      token, so the definition is localized out of a 20-page manuscript (and, with
#      several files, ranked across them at once). Cost is (k chunks x reader), not
#      document length — this is what keeps it doc-scale.
#   2. **Read** — hand the top-k chunks to a pluggable `ProseReader`. The default
#      deterministic reader extracts only a *cued* definition (forward `mass - fish
#      mass`, reversed `fish mass (mass)`), splitting trailing units — high
#      precision, no free-sentence guessing. A narrative document whose prose has no
#      such cue is the LLM reader's job (the seam below); until one is injected the
#      reader abstains, which is the honest outcome.
#
# It emits `_Candidate(method="prose_read")`, registered at the *same* tier as the
# glossary regex, so `_decide` treats the two as corroborating/conflicting evidence
# with no change to its logic. The reader is opt-in (`resolve_catalog(...,
# prose_reader=...)`); passing none reproduces today's behavior exactly.
#
# **Call shape (batched + cached).** A naive per-(column, chunk) loop is the worst
# pattern for an expensive backend: an LLM reader would pay one round-trip per column
# per chunk. But definitions cluster — a manuscript's Methods section defines many
# columns in the same few paragraphs — so the retriever is **chunk-major**: it maps
# each distinct retrieved chunk to the columns that reached it and calls the reader
# **once per chunk** over all of them (`ProseReader.read_many`). Cost then scales with
# *distinct retrieved chunks*, not column count. `CachedProseReader` memoizes by
# (column, chunk) — negatives included — so repeats and overlapping bundles are free.
# The deterministic reader is cheap enough that this is invisible; the shape exists so
# an LLM reader is affordable the day it is wired.


@dataclass(frozen=True)
class ReadResult:
    """What a :class:`ProseReader` extracts from a localized chunk."""

    description: str
    units: Optional[str] = None
    confidence: str = "medium"      # a reader may temper this; capped at its tier's base


class ProseReader:
    """The seam: read a meaning for a column out of one retrieved ``chunk``.

    Two entrypoints. :meth:`read` handles one column; :meth:`read_many` handles all
    columns that retrieved a given chunk in a single shot — the batched path the
    resolver actually calls. Implement whichever fits the backend: a cheap
    per-column reader (the deterministic floor) implements ``read`` and inherits the
    default ``read_many`` that loops it; a batched backend (an LLM answering many
    columns per chunk) overrides ``read_many``. Return ``None`` / omit a column to
    abstain. Retrieval, caching, and :func:`_decide` are all external, so any reader
    plugs in identically.
    """

    def read(self, *, column: str, dtype: str, chunk: str) -> Optional[ReadResult]:
        raise NotImplementedError

    def read_many(
        self, *, columns: List[Tuple[str, str]], chunk: str
    ) -> Dict[str, ReadResult]:
        """Read every ``(column, dtype)`` against one chunk; keyed by column name.

        Default: loop :meth:`read`. A batched backend overrides this to answer all
        columns for the chunk in one call. Columns that abstain are simply absent.
        """
        out: Dict[str, ReadResult] = {}
        for name, dtype in columns:
            result = self.read(column=name, dtype=dtype, chunk=chunk)
            if result is not None:
                out[name] = result
        return out


class CachedProseReader(ProseReader):
    """Memoize a reader by (column, dtype, chunk) so a span is read at most once.

    Wraps any :class:`ProseReader`. The cache key hashes the chunk text, so the same
    passage retrieved by several columns, re-seen across tables of a bundle (the same
    instance is threaded through every ``resolve_catalog``), or hit on a re-run costs
    nothing. **Negatives are cached too** — an abstention is a result worth not
    recomputing, which matters most for an LLM backend. Keep one instance alive across
    the bundle to share its cache.
    """

    def __init__(self, inner: ProseReader) -> None:
        self._inner = inner
        self._cache: Dict[Tuple[str, str, str], Optional[ReadResult]] = {}

    def read(self, *, column: str, dtype: str, chunk: str) -> Optional[ReadResult]:
        return self.read_many(columns=[(column, dtype)], chunk=chunk).get(column)

    def read_many(
        self, *, columns: List[Tuple[str, str]], chunk: str
    ) -> Dict[str, ReadResult]:
        digest = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
        out: Dict[str, ReadResult] = {}
        misses: List[Tuple[str, str]] = []
        for name, dtype in columns:
            key = (name, dtype, digest)
            if key in self._cache:
                cached = self._cache[key]
                if cached is not None:
                    out[name] = cached
            else:
                misses.append((name, dtype))
        if misses:
            fresh = self._inner.read_many(columns=misses, chunk=chunk)
            for name, dtype in misses:
                result = fresh.get(name)
                self._cache[(name, dtype, digest)] = result   # cache the abstention too
                if result is not None:
                    out[name] = result
        return out


_SENTENCE_SPLIT = re.compile(r"[.;\n]+")
_TRAILING_UNITS = re.compile(r"\s*\(([^)]{1,24})\)\s*$")


def _split_trailing_units(text: str) -> Tuple[str, Optional[str]]:
    """Peel a trailing parenthetical: ``fish mass (g)`` -> (``fish mass``, ``g``)."""
    m = _TRAILING_UNITS.search(text)
    if m:
        return text[: m.start()].strip(), m.group(1).strip()
    return text.strip(), None


def _forward_definition(name: str, sentence: str) -> Optional[str]:
    """``mass - fish mass``/``mass: fish mass`` *within a localized sentence*."""
    m = re.search(
        rf"(?<![A-Za-z0-9_])[`'\"]?{re.escape(name)}[`'\"]?(?:\s*[:=]\s*|\s+[–—-]\s+)"
        rf"([A-Za-z][A-Za-z0-9 /()%-]{{2,80}})",
        sentence,
        re.IGNORECASE,
    )
    return m.group(1).strip().rstrip(".;,") if m else None


def _reversed_definition(name: str, sentence: str) -> Optional[str]:
    """``fish mass (mass)`` / ``fish mass `mass``` — the phrase *before* the token."""
    m = re.search(
        rf"([A-Za-z][A-Za-z0-9 /()%-]{{2,80}}?)\s*[\(\[`'\"]{re.escape(name)}[\)\]`'\"]",
        sentence,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


class DeterministicProseReader(ProseReader):
    """The LLM-free floor: return a meaning only when a definitional *cue* is present.

    Scoped to a chunk the retriever already localized, it looks in each sentence that
    mentions the token for a forward or reversed definition and splits off trailing
    units. It deliberately does **not** treat an arbitrary mentioning sentence as a
    definition — that keeps precision high and abstention honest; narrative prose with
    no cue is left to an LLM reader. Per-column and cheap, so it uses the inherited
    :meth:`read_many` (a loop) unchanged.
    """

    def read(self, *, column: str, dtype: str, chunk: str) -> Optional[ReadResult]:
        token = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(column)}(?![A-Za-z0-9_])", re.I)
        for sentence in _SENTENCE_SPLIT.split(chunk):
            if not token.search(sentence):
                continue
            for extract in (_forward_definition, _reversed_definition):
                phrase = extract(column, sentence)
                if phrase:
                    desc, units = _split_trailing_units(phrase)
                    if len(desc) >= 3:
                        return ReadResult(desc, units, "medium")
        return None


class LLMProseReader(ProseReader):
    """Seam for an LLM-backed reader — the ceiling for genuine narrative prose.

    Not wired here. Override :meth:`read_many` (**not** ``read``): the resolver calls
    the batched entrypoint, and the whole point of an LLM backend is to answer *all*
    the columns that retrieved a chunk in **one** call — "given this passage, define
    the ones it defines" — so cost is one round-trip per chunk, not per column. Wrap
    the instance in :class:`CachedProseReader` and keep it alive across the bundle so
    each distinct chunk is read once. Ask for structured output and abstention is
    first-class (omit the column). Retrieval and :func:`_decide` are unchanged.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def read_many(
        self, *, columns: List[Tuple[str, str]], chunk: str
    ) -> Dict[str, ReadResult]:
        raise NotImplementedError("inject an LLM client and implement read_many()")


# Chunks retrieved per column before reading — enough to survive a mis-ranked top
# hit, small enough to stay doc-scale (cost is k reads per unresolved column).
_PROSE_READ_K = 3


def _batch_prose_reads(
    fields: List[Tuple[str, str]],
    docs: List[TextContext],
    reader: ProseReader,
    *,
    k: int = _PROSE_READ_K,
) -> Dict[str, List[Dict[str, Any]]]:
    """Retrieve each column's top-``k`` chunks, then read them **chunk-major**.

    Retrieval is per column (BM25 over every chunk of every document by the column
    token), but reading is grouped: each distinct retrieved chunk is read **once**,
    over all columns that reached it (:meth:`ProseReader.read_many`), so an expensive
    backend pays one call per chunk rather than per (column, chunk). Returns, per
    column, the ranked candidate reads (description/units/confidence + the chunk's own
    offset as citation) — the exact shape :func:`_resolve_column` folds in as
    ``prose_read`` candidates, so ordering and content match the per-column path.
    """
    # Chunk once per document and reuse the tokenization across every column's query.
    chunks: List[TextChunk] = [c for doc in docs for res in doc.resources for c in doc.iter_chunks(res)]
    if not chunks:
        return {}
    tokenized = [tokenize(c.text) for c in chunks]

    # Per column: its top-k chunk indices (ranked). Per chunk: the columns that reached
    # it — the batch each read_many call covers.
    ranked_for: Dict[str, List[int]] = {}
    cols_by_chunk: Dict[int, List[Tuple[str, str]]] = {}
    for name, dtype in fields:
        query = content_terms(name)
        if not query:                  # opaque/stopword-only names (``la``) can't retrieve
            continue
        scores = bm25_scores(query, tokenized)
        top = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: scores[i], reverse=True)[:k]
        if not top:
            continue
        ranked_for[name] = top
        for i in top:
            bucket = cols_by_chunk.setdefault(i, [])
            if (name, dtype) not in bucket:
                bucket.append((name, dtype))

    # One batched read per distinct retrieved chunk.
    reads_by_chunk: Dict[int, Dict[str, ReadResult]] = {
        i: reader.read_many(columns=cols, chunk=chunks[i].text) for i, cols in cols_by_chunk.items()
    }

    # Assemble each column's candidates in retrieval-rank order.
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, indices in ranked_for.items():
        found: List[Dict[str, Any]] = []
        for i in indices:
            result = reads_by_chunk.get(i, {}).get(name)
            if result:
                chunk = chunks[i]
                found.append({
                    "description": result.description,
                    "units": result.units,
                    "confidence": result.confidence,
                    "evidence": f"{chunk.resource}#{chunk.start_offset}",
                })
        if found:
            out[name] = found
    return out


# ---------------------------------------------------------------------------
# Value priors (the recomputable floor) and cross-check
# ---------------------------------------------------------------------------


# A float in [-90, 90] is *not* self-evidently a coordinate — fish mass, pH, and
# most small measures fit the range too. So the coordinate prior requires the column
# *name* to corroborate (a latitude/longitude token, or a bare la/lo/x/y), turning a
# guess into a name-plus-value agreement. Without a name signal the column stays
# "numeric" and abstains, rather than being mislabelled a coordinate.
_COORDINATE_NAME = re.compile(r"lat|lon|coord|northing|easting", re.I)


def _looks_like_coordinate_name(name: Any) -> bool:
    n = str(name if name is not None else "").strip().lower()
    return bool(_COORDINATE_NAME.search(n)) or n in {"la", "lo", "x", "y"}


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
        # Coordinate requires float values in range *and* a corroborating name
        # (see _looks_like_coordinate_name): integers and generically-named floats
        # (mass, pH) are not coordinates even when they fall in [-90, 90]. Even then a
        # single column cannot tell latitude from longitude, hence "medium" and a
        # description that says so; a dictionary link, when present, overrides it.
        coord = (
            pd.api.types.is_float_dtype(series)
            and detect_coordinate_values(series)
            and _looks_like_coordinate_name(series.name)
        )
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
    prose_reader: Optional[ProseReader] = None,
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

    # Retrieve-then-read is computed once for the whole table, chunk-major, so a
    # reader sees each retrieved chunk a single time across all columns (see
    # _batch_prose_reads); empty when no reader was supplied.
    reads = (
        _batch_prose_reads([(f.name, f.dtype) for f in info.fields], docs, prose_reader)
        if prose_reader is not None
        else {}
    )

    resolved: List[ResolvedColumn] = []
    for f in info.fields:
        profile = _value_profile(frame[f.name]) if f.name in frame.columns else {"numeric": False}
        resolved.append(
            _resolve_column(
                f.name, f.dtype, resource, profile, dictionaries, docs, reads.get(f.name, ())
            )
        )
    return Catalog(resource=resource, columns=resolved)


def resolve_bundle(
    targets: List[TabularContext],
    sources: Optional[List[ExecutionContext]] = None,
    prose_reader: Optional[ProseReader] = None,
) -> Catalog:
    """Resolve several data tables into one catalog spanning all their columns.

    A real repository is many tables — the fields of one schema are answered by
    columns in *different* tables (a dataset table, a measurement table, a taxonomy
    table). This resolves each target independently (its columns described from
    ``sources`` — codebooks and documents — and cross-checked against its own
    values), then concatenates the resolved columns into a single catalog. Because
    every :class:`ResolvedColumn` keeps its ``resource``, the router ranks a field
    against every table's columns at once and the compiler groups extraction by
    table — no other layer changes.

    The auxiliary ``sources`` (dictionaries, prose) are offered to *every* target, so
    a shared codebook resolves the columns it covers wherever they live. A column
    name may legitimately occur in two tables; each is resolved on its own values, so
    lookups that need to disambiguate use :meth:`Catalog.find` with the resource.
    """
    sources = sources or []
    columns: List[ResolvedColumn] = []
    for target in targets:
        columns.extend(resolve_catalog(target, sources=sources, prose_reader=prose_reader).columns)
    resource = targets[0].resources[0] if targets else ""
    return Catalog(resource=resource, columns=columns)


# Assurance tiers, most authoritative first. Both prose methods — the glossary
# regex and the retrieve-then-read reader — share tier 2, so when both fire _decide
# treats them as same-tier corroboration or conflict with no special-casing.
_TIER_RANK = {
    "structured_dictionary": 3,
    "lexical_prose": 2,
    "prose_read": 2,
    "value_prior": 1,
}


@dataclass(frozen=True)
class _Candidate:
    """One proposed resolution for a column, from one source."""

    description: Optional[str]
    units: Optional[str]
    method: str
    confidence: str          # the tier's base confidence
    evidence: str

    def summary(self) -> str:
        return f"{self.method} '{self.description or self.units or '?'}'"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description, "units": self.units,
            "method": self.method, "confidence": self.confidence,
            "evidence": self.evidence,
        }


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def _resolve_column(name, dtype, resource, profile, dictionaries, docs, prose_reads=()) -> ResolvedColumn:
    """Gather every candidate resolution for the column, then decide among them.

    ``prose_reads`` are this column's retrieve-then-read candidates, precomputed in
    one batched pass (:func:`_batch_prose_reads`) so an expensive reader is called
    per chunk, not per column; empty when no reader was supplied.
    """
    label = profile.get("label")
    candidates: List[_Candidate] = []

    for d in dictionaries:
        entry = d.by_name.get(name.lower())
        if entry and (entry["description"] or entry["units"]):
            candidates.append(_Candidate(
                entry["description"], entry["units"], "structured_dictionary",
                "high", f"{d.resource} row '{entry['key']}'",
            ))

    for pc in _prose_candidates(name, docs):
        candidates.append(_Candidate(
            pc["description"], None, "lexical_prose", "medium", pc["evidence"],
        ))

    # The retrieve-then-read tier (opt-in). Appended *after* the glossary regex so,
    # on a same-tier tie, the cheap high-precision glossary line wins source-order;
    # a differing read surfaces as a tier-2 conflict rather than silently overriding.
    for rc in prose_reads:
        candidates.append(_Candidate(
            rc["description"], rc["units"], "prose_read", rc["confidence"], rc["evidence"],
        ))

    if label in _SELF_EVIDENT:
        description, confidence = _SELF_EVIDENT[label]
        candidates.append(_Candidate(
            description, None, "value_prior", confidence,
            f"value profile of '{name}' ({dtype})",
        ))

    return _decide(name, dtype, resource, label, profile, candidates)


def _decide(name, dtype, resource, label, profile, candidates) -> ResolvedColumn:
    """Choose among candidates: top tier wins, the value profile referees conflicts."""
    if not candidates:
        # Abstain — nothing describes this column and its values cannot name it.
        # "Unresolved" is a first-class outcome; a fabricated label is worse.
        return ResolvedColumn(resource=resource, name=name, dtype=dtype, value_label=label)

    top_rank = max(_TIER_RANK[c.method] for c in candidates)
    top = [c for c in candidates if _TIER_RANK[c.method] == top_rank]

    def refuted(c: _Candidate) -> List[str]:
        return _cross_check(c.description, c.units, profile)

    # The value profile is the referee: prefer candidates the values don't refute.
    consistent = [c for c in top if not refuted(c)]
    pool = consistent or top
    chosen = pool[0]                       # source order breaks a remaining tie

    # Corroboration is the positive counterpart of a conflict: any source, any
    # tier, that makes the *same* claim as the chosen one (verbatim for now;
    # semantic agreement of differently-worded descriptions needs an LLM). Its
    # citations are recorded so provenance can show who confirmed the resolution.
    def agrees(c: _Candidate) -> bool:
        return (_norm(c.description), _norm(c.units)) == (_norm(chosen.description), _norm(chosen.units))

    corroborators = [c for c in candidates if c is not chosen and agrees(c)]
    corroborated_by = [c.evidence for c in corroborators]

    conflicts: List[str] = list(refuted(chosen))   # the chosen claim's own value conflicts
    for c in top:                                  # a same-tier claim the values rejected
        if c is not chosen:
            conflicts += [f"{c.summary()}: {msg}" for msg in refuted(c)]

    variants = {(_norm(c.description), _norm(c.units)) for c in pool}
    contested = len(variants) > 1
    if contested:
        conflicts.append(
            "sources disagree: " + "; ".join(sorted(c.summary() for c in pool))
        )

    if refuted(chosen):
        confidence = "low"                 # the chosen claim is contradicted by the data
    elif any(c is not chosen and refuted(c) for c in top):
        confidence = "medium"              # the values adjudicated a same-tier conflict
    elif contested:
        confidence = "medium"              # differing claims, unadjudicated
    elif corroborators:
        confidence = "high"                # another source makes the same claim — corroborated
    else:
        confidence = chosen.confidence     # a single source, at its tier's base

    alternatives = [c.to_dict() for c in candidates if c is not chosen]
    return ResolvedColumn(
        resource=resource, name=name, dtype=dtype,
        description=chosen.description, units=chosen.units,
        link_method=chosen.method, link_confidence=confidence,
        link_evidence=chosen.evidence, value_label=label,
        conflicts=conflicts, corroborated_by=corroborated_by, alternatives=alternatives,
    )
