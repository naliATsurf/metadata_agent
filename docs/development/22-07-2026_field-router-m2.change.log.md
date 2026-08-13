# Change log 2026-07-22 — Field router M2: catalog resolution (symbol linking)

**Goal:** Land milestone 2 of the field-driven router
([plan_field_router.md](plan_field_router.md), layer 3) — the pre-routing pass
that closes the semantic gap M1 exposed. `TabularContext.search` cannot reach a
latitude column named `la`: the signal isn't in the name. But it usually *is*
elsewhere in the bundle — a codebook row, a README line — so this is a
**missing-context problem, not a retrieval-tuning one**. Catalog resolution
imports that missing context into the catalog *before* routing, turning each
opaque column into a described one, and cross-checks every borrowed description
against the actual values so a stale codebook is flagged rather than trusted.

## What resolution produces

`src/router/catalog.py`. `resolve_catalog(target, sources) -> Catalog`, where a
`Catalog` is a list of `ResolvedColumn`s. Each records not just the resolved
meaning but *how* it was resolved and on what evidence:

```
ResolvedColumn{ name, dtype, description, units,
                link_method, link_confidence, link_evidence,
                value_label, conflicts, corroborated_by, alternatives }
```

`link_evidence` is the citation (`"codebook row 'la'"`, `"README#565"`,
`"value profile of 'la'"`), so the interpretation is grounded, not asserted —
the two-hop grounding the plan calls for (the value is computed; the meaning
rests on a citation). `conflicts`, `corroborated_by`, and `alternatives` carry
the multi-source picture (see the follow-up below).

## The escalation — most authoritative first

`sources` are the other resources in the bundle, auto-classified: a tabular
source whose column *values* cover the target's column *names* is a data
dictionary; text sources are prose. `_resolve_column` ranks candidates by tier
(the multi-source follow-up replaced the original first-hit shortcut):

1. **Structured dictionary** (`link_confidence="high"`). `_as_dictionary`
   recognises a codebook by key-column coverage, then reads its label/units/notes
   columns by name. This is the fixture's authoritative path: `la` →
   "Latitude of the survey point", units "decimal degrees".
2. **Lexical prose** (`"medium"`). `_prose_definition` finds a definition like
   ``la = latitude`` / ``la: latitude`` in a document and takes the phrase.
3. **Self-evident value type**, then **abstain**. Values identify only the few
   kinds they genuinely reveal — a coordinate range (`medium`, since a value
   cannot tell latitude from longitude) or a parseable date (`high`). A column of
   the long tail — a generic numeric measure, a categorical code — that no
   dictionary or prose describes is left **unresolved** (`link_method="none"`),
   not given a meaningless "numeric" label. "Unresolved" is a first-class outcome.

## Cross-check — the value profile as referee, not identifier

The value profile's dependable role is **refutation**. It cannot name most
variables (a float in some range could be pH, biomass, or reflectance), but it can
*contradict* a specific claim, which is a far weaker and safer job. `_cross_check`
compares a dictionary/prose claim against the values and flags disagreement rather
than trusting the text. On the fixture it catches the planted trap: the codebook
says `tmp` is **Kelvin**, but the values are 4–22, so resolution reports
`tmp: claimed units Kelvin, but values 4.1–21.9 lie in the Celsius range`. The same
rules cover out-of-range latitude/longitude and percentage claims. Refutation runs
whether or not the profile could identify the column — it need not know what `tmp`
is to know it is not Kelvin.

## Closing the gap, verifiably

`Catalog.search` runs the same BM25 ranking as `TabularContext.search` but over
the *enriched* documents (name + resolved description + units). The result, on the
fixture: `tab.search("latitude")` returns `[]` (opaque name), while
`resolve_catalog(...).search("latitude")` returns column `la`. The gap is closed
by importing context, not by a cleverer scorer — and the test asserts both halves
of that contrast.

## Two corrections to the value profile

The design was sharpened after review against two real-world objections.

**It over-identifies (problem 1).** A value profile is diagnostic only for a small
set of self-evident kinds; for most scientific columns "numeric, [0.3, 8.7]" names
nothing. The first cut labelled any numeric in [−90, 90] a "coordinate" (counts,
temperatures included) and gave every column *some* label. Now the value prior
resolves **only** coordinate and temporal, and the long tail **abstains**
(`link_method="none"`) with its `value_label` kept as metadata but no fabricated
meaning. The profile's primary role moved from identifier to **referee** (the
cross-check above). Two residual honesty notes: the coordinate prior is restricted
to **float** columns and rated **medium** (a value cannot tell latitude from
longitude); and `tmp` — a float measurement in [−90, 90] — still reads as
"coordinate" under the *value-only* floor, the irreducible limit of a value prior,
overridden by the dictionary in the realistic path.

**It must not scan every row (problem 2).** The unit of resolution is the column,
not the row. Value profiles are computed from a **sample** (`_PROFILE_SAMPLE`
rows), never a full-table scan — approximate stats suffice for a prior and for
refutation. The only tables read structurally are column-scale *codebooks* (one
row per column); a source is accepted as a dictionary only when a column is
*mostly* schema names (precision) and *unique* (`_DICTIONARY_KEY_PRECISION`,
`_DICTIONARY_KEY_UNIQUENESS`) — which keeps a row-scale *data* table, whose cells
are repeated observations, from ever being mistaken for one. Cost follows the
schema and the docs, not the row count.

## Follow-up: edge cases and the partial-codebook cliff

Reviewing the resolver against real messiness (the happy-path test resolved every
column from a full codebook — too simple) surfaced a sharp edge and some
documented limits.

- **The coverage cliff, fixed.** Acceptance keyed on *recall* (≥ 50% of the
  schema's columns) discarded a whole source below the bar — so a codebook
  documenting 3 of 8 columns contributed *nothing*, even the 3 it defined.
  Acceptance now keys on the **key column's precision and uniqueness**, not
  recall, so a partial codebook resolves the rows it covers and the rest fall
  through the cascade. A decoy data table with a coincidental name match, or a
  constant column equal to a schema name, is still rejected (low precision or low
  uniqueness).
- **Limits captured as tests, not hidden.** `tests/test_catalog.py`'s
  `CatalogEdgeCaseTest` locks the fix and marks a known gap: a **wrong categorical
  description** passes because the cross-check is numeric-only. Surfaced, not
  papered over.

## Follow-up: multi-source resolution (no more first-wins)

The resolver used to stop at the *first* dictionary with an entry — silently
dropping both corroboration (agreeing sources should raise confidence) and
conflict (disagreeing sources should be surfaced, not decided by list order). It
now **gathers every candidate** for a column — from every dictionary, every prose
definition, and the value prior (`_resolve_column`) — and decides among them
(`_decide`):

- **Assurance tier wins** — dictionary > prose > value prior.
- **The value profile referees a same-tier conflict.** Two codebooks that disagree
  on units — one says `temp` is Kelvin, the other Celsius, Kelvin listed first —
  are adjudicated by the values: 4–21 refutes Kelvin, so **Celsius wins** and the
  refutation is recorded. The computed fact breaks the tie between quoted claims.
- **Corroboration raises confidence; contestation lowers it.** Two documents
  defining the same token verbatim → `high` (a single prose is `medium`); two
  sources with differing descriptions → `medium` (contested) with the disagreement
  in `conflicts`; a claim the values refute → `low`.
- **Agreement is recorded, not just counted.** Corroboration was a silent
  confidence bump, with the agreeing source indistinguishable from a loser in
  `alternatives`. It is now a first-class, citable outcome: `corroborated_by` lists
  the citations of every source that makes the same claim as the chosen one — the
  positive counterpart of `conflicts`, so provenance shows *who confirmed* the
  resolution (`link_evidence` stays the single primary citation). Confidence keys
  off the same signal, so record and grade agree.
- **Nothing is discarded silently.** The chosen resolution keeps the losing
  candidates in `ResolvedColumn.alternatives`, `conflicts` carries source-vs-source
  disagreements alongside the value cross-checks, and `corroborated_by` carries the
  confirmations.

Deterministic scope: this adjudicates **units, value-refutable claims, and
verbatim agreement**. Telling *agreement from conflict between differently-worded
free-text descriptions* is semantic and needs an LLM — deferred; that is why two
codebooks with different prose descriptions are marked contested rather than
merged. Tests: `CatalogEdgeCaseTest` — conflict surfaced (not first-wins) with no
corroboration, value-profile unit adjudication, prose and codebook corroboration
recorded with citations, and alternatives retained.

## Verification

- `pytest` — **151 pass, 1 skip** (was 121 after M1; the count grew with the
  edge-case, multi-source, and M3 router tests). One unrelated pre-existing
  failure (`test_connections`'s `SurfConnection`) fails on the clean tree too.
- New tests: `tests/test_catalog.py` — structured-dictionary resolution, the
  units cross-check conflict, the gap-closing contrast (raw search empty →
  enriched search finds the column), self-evident value types resolving
  (coordinate/temporal) while the **long tail abstains** (numeric/categorical →
  `link_method="none"`), lexical-prose resolution, a non-dictionary source
  ignored, and an out-of-range coordinate claim flagged — plus `CatalogEdgeCaseTest`
  for the partial-codebook cliff and multi-source conflict/corroboration.
  Self-contained (build their own tiny bundles), so they don't depend on the
  gitignored fixture.
- Demonstrated on `data/tests/router_test/`: all eight columns resolved via the
  codebook, the `tmp` Kelvin conflict surfaced, and `search("latitude")` /
  `search("air temperature")` reaching `la` / `tmp`.

## Follow-up: hardening against a real dataset (en-dash prose, coordinate gate)

Running catalog resolution on a real repository (a 6-table fish-physiology dataset
with a prose `Readme.txt`, no codebook) exposed two defects the synthetic fixtures
did not, both now fixed:

- **Prose linking missed the real glossary and matched compound words.** The
  glossary used an **en-dash** (`pH – acclimation pH`) that the separator class
  `[:=—-]` (em-dash + hyphen) did not include, so real definitions failed — while the
  bare hyphen matched *inside* words (`tank-level` → a bogus `tank` definition). The
  pattern now accepts `:`/`=` **or** a dash (hyphen/en/em) that must be
  **whitespace-surrounded**, and matches **case-insensitively** with a word-boundary
  lookbehind (so a `mass` column finds `Mass – fish mass (g)`, but `id` never matches
  inside `individual`). Resolution on the dataset went from 21/45 (mostly wrong) to
  43/45, all correctly cited to the Readme.
- **The coordinate value-prior mislabelled ordinary floats.** A float in [-90, 90]
  is not self-evidently a coordinate — fish mass, pH, and most small measures fit the
  range too — so the prior labelled 14 columns "geographic coordinate". The
  coordinate prior now requires the column **name** to corroborate (a
  latitude/longitude token, or a bare `la`/`lo`/`x`/`y`), turning a guess into a
  name-plus-value agreement. This also resolves the `tmp` → coordinate residual noted
  below: `tmp` no longer reads as a coordinate. Legitimate `la`/`lo`/`lat`/`lon`
  columns are unaffected.

## Follow-up: retrieve-then-read prose tier (above the glossary regex)

The prose rung was a single whole-document regex looking for one fixed shape
(`term <sep> definition`). That fits a short codebook README and little else: a
**long manuscript** defines a column narratively, scattered across pages and files,
and `search`-ing the whole text for the *first* match yields an arbitrary — usually
wrong — hit (`mass` appears dozens of times before it is ever defined). Multiple or
long description files make both failure modes worse: precision collapses (first
match is rarely the definition) and recall collapses (narrative prose has no
glossary shape). More regex epicycles do not fix this; **retrieval + a reader** do.

A new **retrieve-then-read** tier slots in above the glossary regex, opt-in via
`resolve_catalog(..., prose_reader=...)` / `resolve_bundle(..., prose_reader=...)`:

1. **Retrieve** — `_batch_prose_reads` BM25-ranks *every chunk across every
   document* by the column token, localizing the definition out of a 20-page
   manuscript (and ranking across several files at once). It stays doc-scale.
2. **Read** — the retrieved chunks are handed to a pluggable `ProseReader`. Two
   implementations against that one seam: `DeterministicProseReader`, the LLM-free
   floor, extracts only a *cued* definition — forward (`mass – fish mass`), reversed
   (`fish mass (mass)`) — and splits trailing units, abstaining when no cue is
   present (no free-sentence guessing); `LLMProseReader` is the documented seam for
   the ceiling that reads genuine narrative prose. The winning chunk's offset is the
   citation.

It emits `_Candidate(method="prose_read")` registered at the **same tier as the
glossary regex** (`_TIER_RANK["prose_read"] = 2`), so `_decide` needs no change: when
both prose methods fire they are treated as same-tier corroboration or conflict by
the existing machinery. Appended *after* the regex, so on a same-tier tie the cheap
high-precision glossary line wins source-order and a differing read surfaces as an
honest tier-2 conflict. The reader defaults to `None` everywhere, so existing
behavior and tests are unchanged.

**Call shape — batched and cached, so an expensive reader stays affordable.** A
naive per-(column, chunk) loop is the worst pattern for an LLM: one round-trip per
column per chunk. But definitions cluster — a manuscript's Methods section defines
many columns in the same few paragraphs — so reading is **chunk-major**:
`_batch_prose_reads` maps each distinct retrieved chunk to the columns that reached
it and calls `ProseReader.read_many` **once per chunk** over all of them. Cost then
scales with *distinct retrieved chunks*, not column count. The seam carries both
entrypoints: a cheap per-column backend implements `read` and inherits the default
`read_many` (a loop); a batched backend (the LLM) overrides `read_many` to answer
every column for a chunk in one call. `CachedProseReader` wraps any reader and
memoizes by (column, chunk) — **negatives included** — and, because the same
instance is threaded through every table of a `resolve_bundle`, a chunk shared
across tables or re-seen on a re-run is read once. The which-columns policy stays
simple: the reader runs on every column (so it can corroborate a regex hit), and the
batching/caching is what makes "every column" cheap rather than gating columns out.

On the real 6-table dataset, enabling the deterministic reader is not a no-op: it
**corroborates** the Readme glossary lines the regex already reads, lifting 25 of 43
columns to `high` confidence (same claim, same tier → corroboration); the 18 where
the two extract differently stay `medium`, and the 2 genuinely-undefined columns
still abstain. Tests: `ProseReaderTierTest` — the reader's forward/reversed/abstain
extraction, the opt-in gate (a reversed definition invisible without a reader),
retrieval localizing the defining document among decoys, same-tier
corroboration/conflict through `_decide`, and the call shape (one `read_many` per
chunk covering both columns; `CachedProseReader` reading each chunk once including
negatives and sharing its cache across a bundle's tables).

## Scope and limits

- **The value profile identifies a narrow set by design.** Only coordinate and
  temporal resolve from values; everything else abstains. This is a choice, not a
  gap: values genuinely cannot name pH from nitrogen. Coordinate now additionally
  requires a corroborating column name (see the follow-up above), so a bare float
  range no longer resolves as a coordinate on its own.
- **Discovery still assumes you name the sources.** `resolve_catalog` takes an
  explicit `sources` list. The real-world case — you *don't* know which file
  documents the columns, and the descriptions are free natural language — needs
  **retrieval-first discovery** (turn each column into a query, rank spans across the
  whole doc corpus, extract with a grounded, cross-checked reader). The
  retrieve-then-read tier (follow-up above) is the first half of that generalization
  — retrieval localizes the span and a pluggable reader interprets it — but the
  deterministic reader still only extracts a *cued* definition; a grounded LLM reader
  over the retrieved chunk is the deferred ceiling. The other deterministic rungs
  remain its high-precision short-circuits.
- **Deferred rungs.** Fuzzy/positional linking (`la`~`lat`) and per-`context_key`
  caching are not implemented. Grounded LLM extraction from free prose now has a
  seam (`LLMProseReader`, see the retrieve-then-read follow-up) but no wired
  implementation.
- **Bundle representation.** `resolve_catalog` takes the auxiliary resources as an
  explicit `sources` list rather than assuming a single multimodal context (that
  unification is separate work). This composes forward: a future multimodal
  context can pass its own other resources as `sources`.
- **Multiple data tables (follow-up).** `resolve_catalog` resolves *one* target
  table. A real repository is many tables, and a schema's fields are answered by
  columns in different ones. `resolve_bundle(targets, sources)` resolves each table
  and concatenates the resolved columns into a single catalog — every
  `ResolvedColumn` keeps its `resource`, so the router ranks a field against all
  tables' columns at once and the compiler groups extraction per table, with no
  change to routing or compilation. A column name can occur in two tables, so
  resource-aware lookup (`Catalog.find(name, resource)`) replaces name-only `get`
  where a routed candidate's assurance is read. Tests: `MultiTableBundleTest`.
- **Not yet wired into the planner.** The router that walks `FieldSpec`s and
  routes each over `Catalog.search` (with `phase="route"`) is M3.
