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
                value_label, conflicts }
```

`link_evidence` is the citation (`"codebook row 'la'"`, `"README#565"`,
`"value profile of 'la'"`), so the interpretation is grounded, not asserted —
the two-hop grounding the plan calls for (the value is computed; the meaning
rests on a citation).

## The escalation — most authoritative first

`sources` are the other resources in the bundle, auto-classified: a tabular
source whose column *values* cover the target's column *names* is a data
dictionary; text sources are prose. `_resolve_column` then escalates and stops
at the first strong hit:

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
row per column); the key-coverage test (`_DICTIONARY_COVERAGE`) is what stops a
row-scale *data* table — whose cells are observations, not column names — from
ever being mistaken for one. Cost follows the schema and the docs, not the row
count.

## Verification

- `pytest` — **130 pass, 1 skip** (was 121 after M1), the 1 warning pre-existing.
- New tests: `tests/test_catalog.py` — structured-dictionary resolution, the
  units cross-check conflict, the gap-closing contrast (raw search empty →
  enriched search finds the column), self-evident value types resolving
  (coordinate/temporal) while the **long tail abstains** (numeric/categorical →
  `link_method="none"`), lexical-prose resolution, a non-dictionary source
  ignored, and an out-of-range coordinate claim flagged. Self-contained (build
  their own tiny bundles), so they don't depend on the gitignored fixture.
- Demonstrated on `data/sample/router_test/`: all eight columns resolved via the
  codebook, the `tmp` Kelvin conflict surfaced, and `search("latitude")` /
  `search("air temperature")` reaching `la` / `tmp`.

## Scope and limits

- **The value profile identifies a narrow set by design.** Only coordinate and
  temporal resolve from values; everything else abstains. This is a choice, not a
  gap: values genuinely cannot name pH from nitrogen. The residual `tmp` →
  coordinate under the value-only floor is the irreducible limit of a float range,
  contained by the escalation order (dictionary wins).
- **Discovery still assumes you name the sources.** `resolve_catalog` takes an
  explicit `sources` list and parses a codebook or matches rigid prose patterns.
  The real-world case — you *don't* know which file documents the columns, and the
  descriptions are free natural language — needs **retrieval-first discovery**
  (turn each column into a query, rank spans across the whole doc corpus, extract
  with a grounded, cross-checked LLM). That is the next generalization, deferred;
  the deterministic rungs here are its high-precision short-circuits.
- **Deferred rungs.** Fuzzy/positional linking (`la`~`lat`), grounded LLM
  extraction from free prose, and per-`context_key` caching are not implemented.
- **Bundle representation.** `resolve_catalog` takes the auxiliary resources as an
  explicit `sources` list rather than assuming a single multimodal context (that
  unification is separate work). This composes forward: a future multimodal
  context can pass its own other resources as `sources`.
- **Not yet wired into the planner.** The router that walks `FieldSpec`s and
  routes each over `Catalog.search` (with `phase="route"`) is M3.
