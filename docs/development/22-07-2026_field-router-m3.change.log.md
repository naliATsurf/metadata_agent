# Change log 2026-07-22 — Field router M3: the router and the FieldPlan

**Goal:** Land milestone 3 of the field-driven router
([plan_field_router.md](plan_field_router.md), layer 4) — the piece that finally
inverts the pipeline. Instead of surveying every source and hoping values fall
out, the router starts from *what it must fill* (the schema's leaf fields) and
routes each to the source that can answer it, emitting a `FieldPlan`: the
persisted routing artifact that M4 will compile into executable `Task`s. This is
the first milestone that runs schema → route end to end, and the first with a
coverage report — a field nothing can answer is flagged *before* extraction, not
discovered as a confabulation afterwards.

## Three buckets, decided standard-agnostically

`src/router/route.py`. `route_fields(schema, catalog, docs)` walks the schema
(`walk_schema`, M1) and sorts each field into one of three routes:

- **structural** — the field's answer is a whole-resource fact (a row count, the
  column list), bound to a deterministic tool, no search, highest assurance.
- **ambiguous-structural** — "which column?". Routed over the **enriched catalog**
  from M2 (`Catalog.search`), so a `spatial_coverage` field reaches the opaque
  column `la` through the description the codebook supplied. This is where M2 pays
  off: the semantic gap, closed in resolution, is now reachable by routing.
- **narrative** — a meaning stated only in prose. Routed over the document sources
  (`TextContext.search`), returning quoted spans.

**No per-standard keyword table decides the bucket.** An earlier cut used two
regexes (`_COUNT_RE`, `_STRUCTURAL_HINT`) tuned to the fixture's field names — a
hidden compatibility table, exactly what the capability design forbids. Removed.
Instead, structural tools **self-declare** via a new `answers_field=True` on
`@context_tool` (marked on `get_item_count` and `get_field_names`), and the router
ranks the field's query against a **structured corpus** — those tools' own
descriptions pooled with the enriched columns, one BM25 ranking since both are
short comparable documents. The winning candidate's *kind* names the bucket: a
tool → structural, a column → ambiguous-structural. A field whose meaning matches
nothing structured — a narrative field — produces no hit there and falls through
to the documents. So a new standard with a field called `n_observations` or
`Anzahl` routes correctly with zero code change, matched by meaning against a
tool's declared purpose rather than a hand-written keyword.

## The FieldPlan and coverage

`FieldPlan{ schema_name, routings: {path: FieldRouting} }`, where a
`FieldRouting{ field_path, query, bucket, candidates, assurance, status }` records
where a field routed and on what candidate evidence. `extractor_role` and
`topology` are on the dataclass but left for the M4 compiler.

Coverage falls straight out: `FieldPlan.coverage()` reports how many fields routed,
by bucket and by assurance, and lists the `unresolved` ones. A field neither corpus
answers is `status="unresolved"` — the `unverifiable` signal, moved upstream of
extraction where it is cheap.

## Two-hop assurance

`_assurance` grades a routing from its top candidate, and for a computed column it
is **two-hop**: the computation is recomputable (high), but the *interpretation* —
that this column means what the field asks — is only as strong as the catalog
resolution behind it, so the weaker hop wins. On the fixture,
`spatial_coverage → la` is **high** because the codebook resolved `la` with high
confidence; drop the codebook and the same field routes to `la` at **medium**,
inheriting the value-only prior's confidence. A retrieved span is quoted-only
(low) until a verifier confirms it — a later milestone.

## Verification

- `pytest` — **137 pass, 1 skip** (was 130 after M2), the 1 warning pre-existing.
- New tests: `tests/test_route.py`. Focused unit tests — structural binding, its
  **standard-agnostic** form (a differently-worded count field still binds via the
  tool description), ambiguous-structural routing, narrative routing, unresolved
  detection, two-hop assurance (high with a codebook, medium without), coverage,
  serialization. Plus `RouterRealisticTest`: a 7-column bundle + codebook +
  multi-section README where routing must **discriminate among competitors** — it
  disambiguates latitude from longitude (lon isn't even a candidate for a latitude
  field), resolves `water_temperature` to `water_temp` over the same-word
  `station_id`, `individual_count` to `abundance` over date/taxon, and `row_count`
  to `get_item_count` over a numeric column. Self-contained.
- Demonstrated on `data/sample/router_test/`: 11/11 fields routed —
  `spatial_coverage` / `temporal_coverage` / `temperature_units` to `la` / `dt` /
  `tmp` at high assurance; `variables` to `get_field_names`; the six prose fields
  to README spans.

## Scope and limits

- **The standard-agnostic ranking exposes the lexical ceiling — honestly.** On the
  fixture, `record_count` ("the number of **observation** records") now routes to
  the `oid` column, whose codebook description is "**Observation** identifier":
  BM25 (no stemming) rates the rare shared term "observation" above the count
  vocabulary, and `get_item_count` doesn't place. The old keyword regex *hid* this
  by hand-forcing the bind; removing it surfaces a genuine limitation. This is the
  same ceiling as narrative-span imprecision (`license`, `methodology` land on
  nearby-but-wrong sections). It is the right trade: the previous version was
  correct only because it was overfit to these exact field names. Stemming and a
  unified embedding space (an open decision) are the real fixes; the milestone's
  job is the architecture, not lexical perfection.
- **A field that wants the whole catalog routes to one candidate.** `variables`
  ("the measured variables and what each means") wants *all* columns, but per-field
  routing returns a top-k; it currently falls back to a doc span. Field grouping is
  a flagged open decision, deferred.
- **No evidence capture yet.** M3 builds the artifact by calling the search
  *methods*. When the router drives execution (M4), those searches fire through the
  `search_context` tool under `attributed_to(Caller(phase="route"))` so each
  becomes captured evidence. That wiring is deliberately not here.
- **Not compiled or executed.** `FieldPlan → Task` compilation (with `Task.fields`,
  the linchpin), per-field topology, the assembly step, and the verify
  reconciliation are M4.
