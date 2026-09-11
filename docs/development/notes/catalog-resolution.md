# Reference: the catalog resolution workflow

This note traces what `resolve_catalog` / `resolve_bundle` (`src/router/catalog.py`)
actually do, in order — which sources are consulted, what is decided deterministically,
when an LLM reader is invoked, and how the result is graded. The module docstring states
the *principle* (a missing-context problem, fixed by importing context into the catalog
before routing); this note states the *sequence*, so the ordering invariants are legible
without reading 1200 lines.

The layer sits between the context layer and the field router: it turns each opaque
column into a *described* column so the router's search can reach it. See
[routing buckets](routing-buckets.md) for what the router then does with the result, and
[the field-router plan](../plans/field-router.md) for the surrounding design.

## Entry points

| Function | Scope |
|---|---|
| `resolve_catalog(target, resource, sources, prose_reader)` | one data table |
| `resolve_bundle(targets, sources, prose_reader)` | many tables → **one** catalog spanning all their columns |

Both take the same auxiliary `sources` (the rest of the bundle) and an optional
`prose_reader`. `resolve_bundle` is the real-repository case: the fields of one schema
are answered by columns in *different* tables, so every `ResolvedColumn` keeps its
`resource` and the router ranks a field against all tables at once.

## Phase 0 — classify the bundle's sources

Each auxiliary source is auto-classified; nothing depends on a filename convention.

- A **`TabularContext`** is tested by `_as_dictionary`: find the column whose *values
  are the target's column names*, ranked by match count then purity, and accept it only
  if precision ≥ `_DICTIONARY_KEY_PRECISION` (0.5) and uniqueness ≥
  `_DICTIONARY_KEY_UNIQUENESS` (0.9). Precision lets a *partial* codebook through;
  uniqueness is what stops a row-scale observation table — repeated values — from being
  read as a codebook. Description / units / notes columns are then picked by name regex,
  yielding a `by_name` map. (`looks_like_dictionary` exposes this same test to a caller
  partitioning a bundle.)
- A **`TextContext`** is kept as a document for the prose tiers.

**The vocabulary the dictionary test is judged against matters.** `resolve_catalog` uses
that one table's columns; `resolve_bundle` passes *the whole bundle's* column names. Without
that, a bundle-wide codebook is mostly *other* tables' names from any single table's point of
view and falls under the precision floor — so one shared codebook would be recognised nowhere.

## Phase 1 — deterministic resolution, per table

`_resolve_table_deterministic`, no LLM involved. It reads a **sample** of the table
(`_PROFILE_SAMPLE` = 1000 rows — approximate stats are enough for a prior and for the
refutation cross-check, and this keeps cost following the schema and the docs, not the
row count), then for each column computes a value profile (`_value_profile`) and calls
`_resolve_column`.

`_resolve_column` **gathers every candidate before choosing any** — three assurance
tiers, ranked by `_TIER_RANK`:

| Tier | Rank | Source |
|---|---|---|
| `structured_dictionary` | 3 | a codebook row keyed on the column name (matched via `_match_key`, whitespace-trimmed, so a stray-space header like `'Nitrate '` still finds its row) |
| `lexical_prose` | 2 | `_prose_candidates` — a glossary-style definition (`la = latitude`) found by regex over a whole document |
| `value_prior` | 1 | only the `_SELF_EVIDENT` labels — coordinates, parseable dates. Values genuinely identify nothing else: "numeric, [0.3, 8.7]" names neither pH nor biomass. The coordinate prior additionally requires the *name* to corroborate (`_looks_like_coordinate_name`), turning a guess into a name-plus-value agreement. |

`_decide` then adjudicates:

1. keep only the **highest tier present**;
2. **the value profile referees** — drop any candidate `_cross_check` refutes; source
   order breaks a remaining tie;
3. record verbatim agreement from *any* source, any tier, as `corroborated_by`;
   disagreement as `conflicts`; every loser as `alternatives`;
4. grade confidence — refuted → `low`; values adjudicated a same-tier conflict, or
   differing claims left unadjudicated → `medium`; corroborated → `high`; otherwise the
   chosen source's own tier base.

With **no candidates at all** the column **abstains** (`link_method="none"`, description
left empty). This is a first-class outcome, not a failure path: a fabricated label is worse
than an honest gap, and the abstention is exactly what gates the next phase.

## Phase 2 — the reader pass, on residuals only

`_read_residuals`, skipped entirely when no `prose_reader` was supplied.

**Residual gating.** Only columns left `link_method == "none"` by Phase 1 are read, which
keeps an expensive reader off every column a codebook or glossary already resolved.

**Bundle-level hoist.** Residual columns from *all* tables are unioned into a single pass,
deduped on `_read_key` (trimmed **and** case-folded) — a prose read depends on the name and
the docs, not the table, so `ID` and `id` in two tables are one read, and a document is read
once for the whole bundle.

**The path is chosen by document size** (`_WHOLE_DOC_MAX_CHARS` = 20 000 chars, set well
above a long README so the common natural-language case skips retrieval):

- **Short docs → `_whole_doc_reads`: no retrieval at all.** The whole text plus *all*
  residual columns go to the reader in one `read_many` per document. This is deliberate,
  not a shortcut: retrieval by column token fails on narrative that never uses the literal
  name ("oxygen debt" for `EPOC`), and when the docs are small, localizing is both
  unnecessary and harmful.
- **A manuscript → `_localized_reads`**, today a pass-through to `_batch_prose_reads`:
  BM25 over every chunk of every document by the column token, top `_PROSE_READ_K` (3)
  chunks per column, then reading grouped **chunk-major** — each distinct retrieved chunk
  is read once over all columns that reached it, so an expensive backend pays one call per
  chunk rather than per (column, chunk). A column whose name is opaque or stopword-only
  (`la`) retrieves nothing and is skipped. A stronger localizer (embedding retrieval with
  the query expanded by dtype and value profile, heading-aware section preference) is
  deferred and marked `TODO(long-doc)`; the token retriever is the honest, limited stand-in.

**Every read is then grounded** by `_ground_read`. The LLM *proposes* a verbatim quote;
locating it *disposes* of it — exact match, then case-insensitive, then whitespace-tolerant
(`_locate`), so only a genuine paraphrase misses. A located quote yields a real span
citation (`resource#start-end`), the text as found in the document, and a grade from
`_grounding_grade` (`high` when the quote both mentions the column and carries the
description's content words, else `medium`). A quote that cannot be located yields the
coarse citation, `low` confidence, and a recorded conflict: the read may still be right, but
its evidence is unconfirmed.

## Phase 3 — re-decide the residuals

Each residual column goes back through `_resolve_column`, now with an **empty** dictionary
and document set (it had no deterministic candidate, by definition) plus its `prose_read`
candidates — and **its value profile still referees the claim**. A grounding conflict is
keyed by the read's evidence, so it attaches only if that read is the one chosen. Reads are
fanned back out on `_read_key`, so a single read reaches every spelling of the column across
the bundle.

The `prose_read` tier deliberately shares rank 2 with `lexical_prose`, with no special-casing:
if a glossary candidate is also present, source order breaks the same-tier tie and a differing
read surfaces as a tier-2 conflict rather than silently overriding.

## Phase 4 — assemble, and what the router sees

Every table's columns are concatenated into one `Catalog`. Each `ResolvedColumn` carries its
`resource`, so the router ranks a field against every table's columns at once and the compiler
groups extraction by table; a name occurring in two tables is disambiguated with
`Catalog.find(name, resource)` rather than `get(name)`.

`Catalog.search(query, k)` is the payoff: the same BM25 ranking as `TabularContext.search`,
but over each column's **enriched** `document()` text rather than its bare header — so a query
for "latitude" now reaches column `la` through its resolved description, closing the semantic
gap that motivated the layer. `Catalog.conflicts` surfaces every column's unresolved
disagreements for reporting.

## Ordering invariants

Three, worth stating separately because they are what the design buys:

1. **Deterministic tiers run before the reader.** Residual gating is what keeps the LLM off
   columns a codebook already answered — cost follows the *unexplained* columns.
2. **Candidates are fully gathered before anything is chosen.** Nothing is decided by list
   position except an explicit same-tier tiebreak; the tier ranking and the value profile do
   the deciding.
3. **The value profile is a referee, not a guesser.** It refutes and corroborates claims from
   the other tiers everywhere, but it *proposes* only for the few self-evident kinds.

## In one paragraph

Catalog resolution classifies the bundle's other resources into data dictionaries (recognised
structurally, by a column whose values are the schema's names) and documents; resolves each
table's columns deterministically by gathering candidates from three assurance tiers —
structured dictionary, glossary prose, self-evident value prior — and choosing the top tier
with the sampled value profile as referee, recording corroboration, conflicts, and losing
alternatives; abstains where nothing describes a column; then, only for those abstentions and
only once for the whole bundle, invokes an optional prose reader, handing it whole documents
when they are small and BM25-localized chunks when they are a manuscript, grounding every read
against its own verbatim quote before re-deciding the column with its value profile still
refereeing; and finally concatenates every table's resolved columns into one catalog whose
enriched per-column documents are what the field router actually searches.
