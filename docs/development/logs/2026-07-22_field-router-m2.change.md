# Change log 2026-07-22 — Field router M2: catalog resolution (symbol linking)

> **Status:** landed, then **extended by later work** — M3 (routing), M4 (the
> FieldPlan→Plan compiler), the multi-table follow-up (`resolve_bundle`), and
> the retrieve-then-read prose reader. The "Scope and limits" section below is
> the M2 snapshot with follow-ups appended inline; items marked *resolved* were
> closed by that later work. For the whole subsystem's current shape see
> [the field-router plan](../plans/field-router.md) and the
> [M4 change log](2026-08-04_field-router-m4.change.md).

**Goal:** Land milestone 2 of the field-driven router
([the field-router plan](../plans/field-router.md), layer 3) — the pre-routing
pass that closes the semantic gap M1 exposed. `TabularContext.search` cannot
reach a latitude column named `la`: the signal isn't in the name. But it usually
*is* elsewhere in the bundle — a codebook row, a README line — so this is a
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
   present (no free-sentence guessing); `LLMProseReader` is the ceiling that reads
   genuine narrative prose (now implemented — see the follow-up below). The winning
   chunk's offset is the citation.

It emits `_Candidate(method="prose_read")` registered at the **same tier as the
glossary regex** (`_TIER_RANK["prose_read"] = 2`), so `_decide` needs no change: when
both prose methods fire they are treated as same-tier corroboration or conflict by
the existing machinery. Appended *after* the regex, so on a same-tier tie the cheap
high-precision glossary line wins source-order and a differing read surfaces as an
honest tier-2 conflict. The reader defaults to `None` everywhere, so existing
behavior and tests are unchanged.

**Cost control — residual gating, bundle hoist, batched + cached.** Three
compounding bounds keep an expensive (LLM) reader affordable:

- **Residual gating.** The reader runs only on columns the deterministic tiers left
  *unresolved* (`link_method == "none"`). It fills genuine gaps and does **not**
  re-read a codebook/glossary line the regex already caught — reading the same prose
  line two ways is not independent corroboration, so inflating confidence on that
  basis was dropped. Cost scales with the genuinely-opaque tail, not the schema.
- **Bundle hoist.** `resolve_bundle` resolves every table deterministically first,
  then unions all tables' residual columns into a **single** `_read_residuals` /
  `_batch_prose_reads` pass over the shared docs — a definition chunk is read once for
  the whole bundle, not once per table.
- **Chunk-major batching + caching.** Within that pass, definitions cluster (a Methods
  section defines many columns in the same paragraphs), so the retriever maps each
  distinct chunk to the columns that reached it and calls `ProseReader.read_many`
  **once per chunk** over all of them — cost scales with *distinct retrieved chunks*,
  not column count. The seam carries both entrypoints: a cheap per-column backend
  implements `read` and inherits the default looping `read_many`; a batched backend
  (the LLM) overrides `read_many` to answer every column for a chunk in one call.
  `CachedProseReader` memoizes by (column, chunk) — **negatives included** — so
  re-runs are free.

Measured on the real 6-table dataset (45 columns, a single-chunk `Readme.txt`): the
deterministic tiers resolve 43/45 via the glossary, leaving **2** residual columns
(genuinely undefined in the Readme). An LLM reader would therefore be invoked **once**
— the hoist unions the 2 residuals across their tables into one call over the one
chunk (un-hoisted, per-table, it would be 2). Not the 45-per-column cost the naive
shape implies. Tests: `ProseReaderTierTest` — the reader's forward/reversed/abstain
extraction; **residual gating** (a regex-resolved column is never handed to the
reader; the reader runs only on the unresolved column); retrieval localizing the
defining document among decoys; the **bundle hoist** (a chunk shared by two tables'
residual columns read once, uncached); and `CachedProseReader` reading each chunk
once including negatives.

## Follow-up: the LLM reader, wired — narrative prose and a doc-size switch

The deterministic tiers all need a *cue* (`term – def`, `def (term)`). Real documentation
is often pure narrative — "Mass is the fish mass in grams", "pH means acclimation pH" —
which has no cue, so on a natural-language readme the glossary regex **and** the cued
reader both resolve **0/45**. That is the common case, and the reason the reader seam
exists. `LLMProseReader` is now a working implementation:

- **Injectable `invoke`.** Its only dependency is a callable `prompt -> text`, so it is
  free of any SDK and trivially stub-tested; `LLMProseReader.from_chat_model(model)`
  adapts a LangChain chat model (the same `create_llm` the players use).
- **Batched, structured, grounded.** It overrides `read_many` (not `read`): one
  structured-JSON call defines *every* column handed to a passage — "define only the
  columns this passage describes; omit the rest." Abstention is first-class (omit a
  column), a malformed/failed call returns `{}` rather than crashing, hallucinated
  columns are dropped, and trimmed names map back to the true header. The **value
  profile still referees** every read in `_decide`, so an LLM claim the data refutes
  (e.g. "latitude" over 100–200) is flagged and demoted, not trusted.

**Doc-size switch (retrieval is not always the right move).** Retrieval by column token
fails when the prose never uses the literal name ("oxygen debt" for `EPOC`). So
`_read_residuals` chooses the read path by document size (`_WHOLE_DOC_MAX_CHARS`):

- **Short docs — skip retrieval** (`_whole_doc_reads`). Hand the reader the *whole* small
  readme plus *all* residual columns in one call, so a column resolves even if its name
  never appears verbatim. This is the natural-language-readme case.
- **Long docs — localize first** (`_localized_reads`). A manuscript can't go to the reader
  whole, so token retrieval (`_batch_prose_reads`) localizes the spans. The stronger
  localizer for a manuscript — embedding retrieval with dtype/value-profile query
  expansion, heading-aware routing — is sketched and deferred (`TODO(long-doc)`).

**Live result.** On the real bundle with a pure-narrative `readme_hard.txt` (every glossary
entry rewritten as prose, column names preserved), against the configured SURF model
(`Qwen2.5-VL-32B`): the deterministic tiers resolve **0/45**; the LLM reader resolves
**43/45** — with units — in **one** live model call for the whole 6-table bundle (whole-doc
mode reads the readme once; `CachedProseReader` shares it across tables). Examples of the
live reads: `EPOC → excess post-exercise oxygen consumption [mg O2 kg-1 h-1]`, `no2 →
blood nitrite concentration [mg/L]`, `masskg → fish mass [kilograms]`. The 2 misses
(`id`, `length`) are the model's recall, not a pipeline gap. Wired into
`examples/resolve_catalog.py` behind `--llm-reader` (with `--doc` to restrict sources).

**Quoted spans (grounded provenance).** A read now carries its *verbatim* supporting
sentence: `ReadResult.quote`, requested in the prompt alongside description/units. After
reading, `_locate` finds that sentence back in the source (exact, then case-insensitive,
then whitespace-tolerant) and the citation is upgraded from a document-level pointer to a
real span
`resource#start-end`, where `source[start:end]` **is** the quoted sentence (a paraphrase
that doesn't locate verbatim falls back to the coarse citation rather than fabricating an
offset). The deterministic reader supplies its matched sentence as the quote too, so both
readers produce located spans. Live, the model cites e.g.
`mass → readme_hard#1452-1483 → "Mass is the fish mass in grams."`, offsets addressing the
exact sentence.

**Grounding grade — deterministic verify-*lite* (the quote as a confidence signal).** The
locate is not just for the citation: whether the quote checks out is itself evidence. The
LLM *proposes* its supporting sentence; locating it *disposes* of the claim, and
`_ground_read` grades the read from three deterministic checks — the quote **locates**
(provenance), **names the column** (relevance), and **carries the description's words**
(support):

- all three → **high** (well-grounded — trusted; the field gets a single extractor);
- located but weak token/word overlap → **medium**;
- quote absent or paraphrased → **low**, with a recorded conflict ("reader evidence
  unconfirmed") — the read may still be right, but its evidence isn't confirmed.

This replaces the flat `medium` every read used to get with an *earned* grade, and it is the
one signal that crosses the only threshold the pipeline acts on (`_topology_for`'s
`== "high"` → single vs. debate). The **value-profile referee stacks on top** unchanged
(`_decide`) — a read the values refute is still forced to `low`. Because the reader enriches
a *column* (`prose_read`), the grade reaches routing through `ambiguous_structural`, which
passes `link_confidence` through (the hardcoded `narrative → "low"` applies to raw document
spans, not catalog reads). Live on `readme_hard.txt`, the flat `medium` became **37 high / 2
medium / 1 low** — the well-grounded reads are now trusted and the unconfirmed ones flagged.

Scope is deliberate: this confirms provenance and *lexical* support, not semantic
entailment (a real, on-topic sentence can still be mis-summarised; lexical overlap won't
catch negation). The entailment check — read the span, confirm it *means* the description,
promote to a `verified` grade / `verified_span` — is the **M5 seam**, deliberately not built
here; `verified_span` stays reserved. Steps done here give M5 a graded, span-cited read to
verify.

Tests use a **stub `invoke`** (no network): parse/abstain, malformed → abstain, name
mapping, dropped hallucinations, `from_chat_model`, the returned quote; the grounding grade
(grounded → high, off-topic located → medium, unlocated → low + conflict, whitespace-tolerant
locate); plus pipeline tests — narrative resolves where the deterministic path returns 0 (one
call); whole-doc mode reads a column whose name never appears; the value profile referees an
LLM read; an LLM read produces an offset-located span whose `source[start:end]` equals the
quote (and a paraphrase falls back to a coarse citation); and a forced-retrieval test keeps
the long-doc path covered.

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
  retrieve-then-read tier (follow-up above) is that generalization, and the free-prose
  half is now covered: the `LLMProseReader` reads narrative (short docs whole, long docs
  localized), grounded by the value-profile referee. What remains deferred is the
  **long-doc localizer** — embedding retrieval and heading routing so a manuscript that
  never uses a column's literal name is still placed (`TODO(long-doc)`). The other
  deterministic rungs remain its high-precision short-circuits.
- **Deferred rungs.** Fuzzy/positional linking (`la`~`lat`) and per-`context_key`
  caching are not implemented. Grounded LLM extraction from free prose **is** now wired
  (`LLMProseReader`, see the follow-up); the deferred piece is the long-doc localizer
  above, not the reader itself.
- **Bundle representation.** `resolve_catalog` takes the auxiliary resources as an
  explicit `sources` list rather than assuming a single multimodal context (that
  unification is separate work). This composes forward: a future multimodal
  context can pass its own other resources as `sources`.
- **Multiple data tables — resolved (follow-up), not a limit.** `resolve_catalog`
  alone resolves *one* target table, but a real repository is many tables and a
  schema's fields are answered by columns in different ones. `resolve_bundle(targets,
  sources)` handles this: it resolves each table and concatenates the resolved
  columns into a single catalog — every `ResolvedColumn` keeps its `resource`, so the
  router ranks a field against all tables' columns at once and the compiler groups
  extraction per table, with no change to routing or compilation. A column name can
  occur in two tables, so resource-aware lookup (`Catalog.find(name, resource)`)
  replaces name-only `get` where a routed candidate's assurance is read. Tests:
  `MultiTableBundleTest`. (Listed here as the one-table→many-table generalization; the
  multi-table case itself is done.)
