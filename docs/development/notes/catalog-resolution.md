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
- A **`TextContext`** is kept as a document for the reader, and each of its files is
  tested by `_as_text_codebook` for the same thing written as text: a glossary. Every
  `term <sep> definition` entry is parsed (`:` / `=`, or a dash with whitespace on both
  sides), and adjacent well-formed entries are grouped into runs. A run is accepted only if
  it has at least `_TEXT_CODEBOOK_MIN_ENTRIES` (3) entries and `_DICTIONARY_KEY_PRECISION`
  of its terms are schema names — the table's precision rule. **A separator alone is never
  the signal**: `AAS = MO2max − MO2standard` in a Methods paragraph has the same `=` as
  `la = latitude`. An entry must also be shaped like a definition (`_is_definition`:
  balanced brackets, no arithmetic, no stray spaced dash, no dangling function word), and a
  malformed entry ends its run. An entry whose definition only restates its term
  (`p50 – p50`) is well-formed, so it keeps its run intact, but it is not recorded: the
  column stays unresolved and open to the reader. Trailing parenthesised units are split off. Isolated
  matches are dropped — they are narrative, and narrative is the reader's.

**The vocabulary the codebook tests are judged against matters.** `resolve_catalog` uses
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
| `structured_dictionary` | 3 | a codebook row keyed on the column name (matched via `_read_key`, trimmed and case-folded, so a stray-space header like `'Nitrate '` still finds its row); base confidence `high` |
| `text_codebook` | 2 | an entry of an accepted glossary run, cited as a `resource#start-end` span; base confidence `medium`. Ranked below a table because a parse of text can split an entry wrongly where a cell cannot — the table wins a disagreement, and the text can only corroborate it |
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
keeps an expensive reader off every column a codebook, table or text, already resolved.

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
  chunks per column. The distinct retrieved chunks are then **packed** in document order
  into passages of up to `_PASSAGE_MAX_CHARS` (20 000), and each passage is read once over
  every column that retrieved a chunk in it — an expensive backend pays per retrieved
  text, not per chunk or column. Each read is grounded in the chunk its quote is found in,
  so citations stay document offsets. A column whose name is opaque or stopword-only
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

When a column has several reads, `_decide` keeps that grade from being overridden: a read
whose quote was found is chosen over one whose quote was not; a chosen unconfirmed read
stays `low` even if other reads disagree or repeat it; and an unconfirmed read never counts
as corroboration.

## Phase 3 — re-decide the residuals

Each residual column goes back through `_resolve_column`, now with **no** codebooks
(it had no deterministic candidate, by definition) plus its `prose_read` candidates — and
**its value profile still referees the claim**. A grounding conflict is keyed by the read's
evidence, so it attaches only if that read is the one chosen. Reads are
fanned back out on `_read_key`, so a single read reaches every spelling of the column across
the bundle.

`prose_read` shares rank 2 with `text_codebook`, but under residual gating the two never
compete for one column: a read only reaches a column no codebook answered. The rank only
places a read above the value prior.

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

Catalog resolution classifies the bundle's other resources into codebooks (recognised
structurally — a table column, or a run of glossary entries, whose keys are the schema's names)
and documents; resolves each
table's columns deterministically by gathering candidates from three assurance tiers —
codebook table, text codebook (a glossary run accepted on its structure, not its
separators), self-evident value prior — and choosing the top tier
with the sampled value profile as referee, recording corroboration, conflicts, and losing
alternatives; abstains where nothing describes a column; then, only for those abstentions and
only once for the whole bundle, invokes an optional prose reader, handing it whole documents
when they are small and BM25-localized chunks when they are a manuscript, grounding every read
against its own verbatim quote before re-deciding the column with its value profile still
refereeing; and finally concatenates every table's resolved columns into one catalog whose
enriched per-column documents are what the field router actually searches.
