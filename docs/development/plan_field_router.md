# Extension Plan: Field-Driven Routing with a `Searchable` Capability

This document plans a planner that fills a metadata standard **field by field**,
routing each field to the source that can answer it, instead of surveying every
source and hoping the values fall out. It builds on the capability split in
[plan_free_text.md](plan_free_text.md), reuses the plan/artifact machinery
described in [plan_multi_modality.md](plan_multi_modality.md), and extends the
evidence-attribution work landed in
[the 2026-07-22 change log](22-07-2026_change.log.md). It complements
[Architecture](../architecture.md).

Status legend: ✅ done · 🟡 partial · 🔲 not started · ⛔ blocked on a decision.

## Motivation and approach

Today the planner is **source-driven**: `Orchestrator._inspect_context` sweeps
every auto-fireable tool over every resource, hands the findings to a planning
LLM, and produces `Task`s that say *who does what to which resource*. Values are
matched back to evidence **after the fact** by `attribute_field`. Two costs
follow, both surfaced repeatedly in practice:

- **It floods.** Every player is handed the whole survey; ~64% of a measured run
  was three tools describing the same context. The 2026-07-22 dedup treats the
  symptom; it does not remove the cause.
- **It cannot reach the narrative fields.** `abstract`, `methodology`, `license`
  live in description documents, not in tabular tool output, so they surface as
  `unverifiable` no matter how hard the tabular tools work.

Real datasets are **sparse per field**: a README answers `abstract`; the CSV
answers `spatial_extent`; a codebook answers `units`. Source-driven planning asks
every source about every field. **Field-driven planning asks each field only
where its answer could live.**

**The approach is one inversion:** start from the target schema's fields, *route*
each to its candidate source(s), then extract from those candidates only. This
dissolves the flood (each field's extraction sees only its candidates —
*retrieval is the curation*) and reaches the narrative fields (documents become a
routable surface).

We **extend, not fork**, consistent with the previous two plans. The router emits
the existing `Plan`/`Task` shape, so `PlanExecutor`, `StepExecutor`, and the
debate topology run unchanged. Work concentrates in `src/context/` (a new
capability), `src/orchestrator/` (a new planner strategy), and `src/provenance.py`
(one new axis).

## The two linchpins

Every design choice and every risk below reduces to two invariants. If either is
compromised, the result is a more complex system that is **less** grounded than
the one we have now.

1. **Field identity must survive compilation.** The `FieldPlan` is the source of
   truth; a `Task` is only its execution form. The field → task → result
   correspondence must be carried explicitly (a `fields` attribute on `Task`),
   never reconstructed by heuristics afterwards — otherwise we are back to
   post-hoc matching and the `FieldPlan` was theatre.
2. **The router proposes; the evidence disposes.** A-priori routing is a
   *hypothesis*. The existing `attribute_field` stays on as its *verifier*:
   confirm the produced value actually traces to the routed candidate, and
   downgrade to `unverifiable` when it does not. Text sources cannot be replayed
   like tabular facts, so without this a grounded-*looking* wrong value is more
   dangerous than an honest gap.

## Naming: `search`, `route`, and what is really searched ⛔→✅ (decided)

"Search" and "locate" both mislead if used for the whole thing. The resolution is
to name by layer:

- **`Searchable.search(query) -> [EvidenceRef]`** — the low-level capability.
  Keep the word *search*: it is honestly ranked and fuzzy and **may return
  nothing**, which is exactly the semantics the abstention logic needs. "Locate"
  is rejected — it over-promises certainty the design must not have.
- **`route_fields(...)`** — the planner verb. Dispatch a field to the surface or
  column that answers it.
- The "it is a pointer, not an answer" meaning lives in the **return type**
  (`EvidenceRef`), not the verb.

### What `search` actually runs over

Search is not one operation; it is *locate candidate surfaces*, and only one of
the three surfaces is text search in the RAG sense. For two of the three, search
returns a **location**, and something downstream turns it into a value.

| Surface | Searched | Returns | Value produced by | Assurance |
|---|---|---|---|---|
| **Document prose** (README, data paper, codebook, license) | chunked + embedded text | a **span** (`readme.md#L40-52`) | LLM **quotes/extracts** | low–medium (quoted) |
| **Schema / identifier catalog** (column, table, file names, dtypes, samples) | identifiers, semantically matched to the field | a **column/table pointer** | a **computation tool** (`get_temporal_extent(col)`) | high (recomputable) |
| **Data values** (rare) | distinct values / header cells | a **column + filter** | aggregate/read | medium |

The tabular case is **not text search** — it is a column router, and a
**generalization of tools we already have**: `detect_spatial_columns` /
`detect_temporal_columns` are hardcoded column routers for two field types. The
generalization is "given *any* field description, rank the columns that could
satisfy it," after which the existing tabular tools do the real, recomputable
work.

### The semantic gap and cross-file symbol resolution

The schema-catalog search assumes column *names* carry signal. Real datasets
violate this constantly: a latitude column named `la`, `l`, or a random code. No
embedding turns `la` into "latitude" — **the signal is not there.** But it is
usually *elsewhere in the bundle*: a codebook, a data dictionary, or a README line
`la = latitude, decimal degrees N`. So this is a **missing-context problem, not a
retrieval-tuning problem**, and the fix is a step that imports the missing context
into the catalog *before* routing.

**Catalog resolution (symbol linking).** A pass, run once per context before the
router, that turns each opaque column into a *described* column by pulling
explanations from the other files. The router then searches the **enriched**
catalog, so the gap is bridged upstream of retrieval — retrieval quality is
downstream of catalog quality.

```
{ name: "la", dtype: float, samples: [10.0, 10.5] }                        # raw
{ name: "la", ..., description: "latitude, decimal degrees",               # resolved
  link_evidence: "codebook.csv#L12", link_confidence: "high" }
```

Linking escalates most-authoritative first, stopping at the first strong hit:

| Signal | Example | Precision |
|---|---|---|
| Structured dictionary parse | a codebook table `variable | description | units`, row `la` | highest (authoritative map) |
| Lexical token match in prose | README: "`la` = latitude" | high when the doc uses the token |
| Value / distributional prior | `la` ∈ [−90,90], paired with `lo` ∈ [−180,180] | high **and recomputable** |
| Fuzzy / positional | `la`~`lat`; doc lists variables in column order | low — must be verified |

Two properties keep this principled rather than a pile of heuristics:

- **The value prior is a referee, not a general identifier.** Values identify only
  a few self-evident kinds — a coordinate range, a parseable date — and say nothing
  about the long tail (pH, biomass, a trait score are all just "numeric, some
  range"). So a column of that long tail *abstains* rather than borrow a
  meaningless label; "unresolved" is a first-class outcome. What the prior is
  reliable for is **refutation**: it need not know what `tmp` is to know its 4–22
  values are not Kelvin. Resolution is also **doc-scale** — profiles come from a
  *sample*, never a full-table scan; a million-row data table is sampled, never
  indexed, and the codebook coverage test keeps it from being mistaken for a
  dictionary.
- **Text link and value prior cross-check.** Agreement → high confidence.
  Disagreement (codebook says latitude, values in [−180,180]) → **conflict,
  surfaced** — a stale codebook is exactly what field-driven routing should
  expose, resolved by the debate machinery.

**Provenance is a two-hop chain.** A value filled from column `la` *because* the
codebook says `la` is latitude rests on the **computation** (recomputable, high)
*and* the **interpretation** (`codebook#L12`, quoted, medium). The field's
assurance is the **weaker hop**, and provenance must cite *both*. Linchpin 2's
verifier confirms not just the value-to-column trace but that the **link** is real
— a wrong link is the failure mode here.

### Not every field is searched

Scope bound: fields fall into three buckets, and only two need search.

1. **Structurally determined** → direct tool binding, no search. `row_count` is
   *always* `get_item_count`; `columns` is *always* `get_field_names`.
2. **Ambiguous-structural** → the *schema router* ("which column is the date?").
3. **Narrative** → *document retrieval* (`abstract`, `license`, `methodology`).

## Data model 🟡

Four new types; the last is the persisted deliverable. `FieldSpec` and
`EvidenceRef` landed in M1 (`src/router/schema.py`,
`src/context/base_context.py`); `FieldRouting` and `FieldPlan` are M3.

```
FieldSpec       { path, description, type, required }          # flattened from the schema
EvidenceRef     { resource, locator, kind, snippet, score }    # a candidate; locator = span | column
FieldRouting    { field_path, query, candidates:[EvidenceRef],
                  assurance, extractor_role, topology }
FieldPlan       { routings: {field_path: FieldRouting}, schema_ref }   # source of truth
```

`kind` on `EvidenceRef` is the assurance carrier: `computed_column` >
`verified_span` > `quoted_span`.

## The pipeline — six layers 🟡

Ordered so each is testable before the next and the migration stays incremental.

1. ✅ **Schema walker → `FieldSpec[]`** (`src/router/schema.py`). Flattens the
   output schema to leaf fields with descriptions, unwrapping
   nested/optional/union Pydantic models; containers are leaves. Pure function.
2. ✅ **`Searchable` capability + `search_context` tool.** `Searchable` +
   `EvidenceRef` in `src/context/base_context.py`; `TabularContext` and
   `TextContext` reparented to it and implement `search`; `search_context` tool
   in `src/tools/search.py`. Funnels through the tool boundary, so retrievals land
   in the evidence ledger with `used_by` (the router sets `phase="route"` in M3)
   and dedupe through `_RESULT_CACHE`.
   - `TextContext.search` → ranks chunks → `quoted_span` refs. (The pre-existing
     literal grep was renamed `TextContext.grep`.)
   - `TabularContext.search` → ranks columns over the identifier catalog →
     `computed_column` refs (generalizing `detect_*_columns`).
   - **Scorer is a lexical baseline** (`tokenize` / `lexical_score`) — dependency-
     free and deterministic. It scores ~0 for an opaque name against a semantic
     query *by design*; the embedding replacement is the open decision below, and
     catalog resolution (layer 3) closes the gap.
3. ✅ **Catalog resolution (symbol linking)** (`src/router/catalog.py`). A
   pre-routing pass that enriches the raw catalog with descriptions harvested from
   the other resources, so opaque columns (`la` → latitude) become routable.
   `resolve_catalog(target, sources)` **gathers every candidate** for a column
   (dictionary tiers → lexical prose → self-evident value type) and decides among
   them: the highest assurance tier wins, the **value profile referees same-tier
   conflicts** (two codebooks disagreeing on units are adjudicated by the values,
   not by list order), agreement raises confidence and disagreement is surfaced in
   `conflicts`/`alternatives`, and a column nothing describes **abstains** rather
   than being mislabelled. The value profile's dependable role is **refutation**,
   never identification of the long tail. `Catalog.search` ranks the *enriched*
   columns, closing the gap `TabularContext.search` cannot. Doc-scale: profiles are
   sampled, never a full scan. Deferred: retrieval-first discovery over unknown
   sources, grounded LLM extraction (incl. semantic reconciliation of differing
   descriptions), fuzzy linking, caching. See
   [The semantic gap](#the-semantic-gap-and-cross-file-symbol-resolution).
4. ✅ **Router → `FieldPlan`** (`src/router/route.py`). `route_fields(schema,
   catalog, docs)` walks the schema and routes each field to a bucket —
   **structural** (a whole-resource tool), **ambiguous-structural** (`Catalog.
   search` over the enriched columns), or **narrative** (document search) —
   producing a `FieldRouting` per field and a `FieldPlan`. **The bucket is
   standard-agnostic:** no keyword table. Structural tools self-declare with
   `answers_field=True` on `@context_tool`, and the router ranks the field query
   against those tools' descriptions pooled with the columns (one BM25 ranking);
   the winning candidate's kind names the bucket, and a field matching nothing
   structured falls through to the documents. Assurance is two-hop for a computed
   column (inherits the catalog resolution's confidence); a field neither corpus
   answers is `unresolved` → `unverifiable` *before* extraction, reported by
   `FieldPlan.coverage()`. M3 emits the artifact by calling the search methods;
   firing them through the tool with `phase="route"` attribution comes with
   execution (M4).
5. ✅ **Compile `FieldPlan` → `Plan(List[Task])`** (`src/router/compile.py`). The
   linchpin-1 step, deterministic and LLM-free:
   - Group routings sharing `(bucket, resource, assurance tier)` into one `Task`,
     **capped by a token budget** so grouping does not re-introduce flooding at the
     group level. The tier is in the key so a high-assurance field is never dragged
     into a contested sibling's debate.
   - Each `Task` carries `fields=[…]` and **`field_bindings`** — the router's
     *ranked* candidate set per field, plus a **select-and-extract** instruction. The
     compiler **proposes a set, it does not commit to top-1**: the executor chooses
     which candidate answers each field (linchpin 2 — router proposes, evidence
     disposes), so correctness needs the router's *recall@k*, not *precision@1*, and
     an under-ranked-but-correct candidate stays reachable. Assurance travels as
     *provisional*, confirmed by the verify pass. `field_bindings` is the *single*
     carrier of candidate evidence (the curated slice — no whole-context survey), and
     `task` is a short action identifier, not a prose instruction.
   - Topology per task from assurance: single extractor for high-assurance
     computed fields, debate only for contested/low-assurance ones.
   - One terminal **assembly `Task`** depends on all extraction tasks (fan-in),
     replacing the monolithic `metadata_generator` synthesis.
   - `PlanExecutor` / `StepExecutor` then run **unchanged**.
6. **Reconcile + verify (linchpin 2).** Walk the `FieldPlan`; for each field find
   its result via the `Task.fields` correlation; build provenance a-priori
   (`source_ref` = the routed candidate); then run `attribute_field` as the
   verifier and downgrade/`unverifiable` on mismatch.

## Provenance: an assurance axis 🔲

`FieldProvenance` gains a grade so text sources cannot masquerade as recomputable
facts (the decision that makes or breaks the text modality):

```
source_type:  context_tool (recomputable) | document_span (quoted)
transform:    computed | derived | quoted
assurance:    high (replayable tabular) | medium (span verified to support value)
                                        | low (span retrieved only)
```

Assurance is derived from **source kind + verification**, never from an
extractor's self-reported confidence (miscalibrated and gameable). Retrieval must
be **re-runnable** even when not bit-stable: persist `query` + top-k refs so a
verifier reruns the same search.

## What stays (reuse map)

| Existing seam | Role under field-driven routing |
|---|---|
| `Plan` / `Task` | unchanged shape; `Task` gains a `fields: List[str]` attribute |
| `PlanExecutor` / `StepExecutor` | unchanged — it still executes a `Plan` of `Task`s |
| debate topology | **conflict resolver** for fields with disagreeing grounded sources — its best real use, not redundant surveying |
| `EvidenceEntry` / `used_by` / `Caller` | unchanged; search hits recorded with `phase="route"` |
| `_RESULT_CACHE` | pattern for a per-`context_key` search index, built lazily, cleared with the context |
| `attribute_field` | repurposed from sole provenance source to **verifier** of the router's hypothesis |
| `detect_spatial_columns` / `detect_temporal_columns` | special cases of `TabularContext.search` |

## Risks and mitigations 🔲

| Risk | Failure mode | Mitigation |
|---|---|---|
| Representation drift | field→result mapping lost in compile; provenance silently guesses | `fields` first-class on `Task` (linchpin 1) |
| Cardinality ≠ 1:1 | fields share candidates, or one field needs several sources | Task tagged with a *set* of fields; assembly gathers each field across tasks |
| Dependency graph reshaped | `validate_task_dependencies` assumes step-to-step data flow | model as fan-out (independent extraction) → fan-in (one assembly) |
| Topology overkill | forcing every field through multi-player debate re-creates the flood cost | choose topology per task by assurance |
| **Semantic gap / opaque names** | column `la` for latitude; the field query matches nothing | catalog resolution imports descriptions from doc surfaces *before* routing; value-prior floor when text linking is absent |
| **Wrong cross-file link** | codebook maps `la`→latitude but it is stale/wrong | cross-check text link against value prior; two-hop provenance cites the link; verifier confirms the link, not just the value |
| Retrieval precision | wrong span → confident wrong value *with a citation* | assurance grade + verify pass + abstention threshold |
| Query quality | thin/jargon `Field(description=...)` retrieves badly, silently | detect low-signal fields, fall back to broad survey; allow query expansion |
| Loss of holistic reasoning | per-field extraction misses cross-field inference the monolithic generator had | final consistency/assembly pass; group coupled fields |
| Conflict resolution | README N=500 vs 480 rows now surfaced, no policy | assurance-ranked resolution, both sources kept; use debate here |
| Determinism vs replay | embeddings/ANN not bit-stable, fights the replay guarantee | grade text as lower-assurance by construction; make search re-runnable |
| Schema introspection | nested/optional/union schemas break "iterate the fields" | dedicated flattening walker (layer 1) |

## Migration path 🔲

Build the router as an **alternative planner strategy** that emits the same
`Plan`/`Task` shape, behind a flag. Run it beside the source-driven planner on the
sample datasets and **diff field-driven vs source-driven output** before switching
anything. Because nothing downstream of the compile step changes, the blast radius
is contained to layers 1–6 and the swap is reversible.

## Open decisions ⛔

1. **Field grouping.** Start flat (schema as a bag of independent fields) or model
   couplings (`units`↔`variable`, `extent`↔CRS) as a field DAG from the start?
   Recommendation: start flat, add grouping when a real dataset forces it.
2. **Assurance grading — commit or not?** Everything above assumes a graded
   provenance. If it stays binary (`filled`/`unverifiable`), text sources dilute
   the guarantee. This is the single decision the text modality's value hinges on.
3. **Index/embedding dependency.** Which embedding model, where the per-dataset
   index lives, and whether one-shot extraction amortizes building it.

## Milestones

- **M1 ✅ — Schema walker + `Searchable`/`search_context`** (layers 1–2). Landed:
  `src/router/schema.py`, `Searchable`/`EvidenceRef`/lexical scorer in
  `src/context/base_context.py`, per-modality `search`, `src/tools/search.py`.
  Tests: `tests/test_router_schema.py`, `tests/test_searchable.py`. The literal
  `TextContext.search` grep was renamed `grep`. No planner change yet.
- **M2 ✅ — Catalog resolution** (layer 3). `src/router/catalog.py`:
  `resolve_catalog` / `Catalog` / `ResolvedColumn`, structured-dictionary +
  lexical-prose + value-prior linking, units/range cross-check, and an enriched
  `Catalog.search`. Tests: `tests/test_catalog.py`. Deferred: fuzzy linking,
  caching.
- **M3 ✅ — Router + `FieldPlan` + coverage report** (layer 4).
  `src/router/route.py`: `route_fields` / `FieldPlan` / `FieldRouting`, three-bucket
  routing over the enriched catalog and document sources, two-hop assurance, and a
  coverage report with pre-extraction `unresolved` detection. Tests:
  `tests/test_route.py`. Compile-to-`Task` and execution are M4.
- **M4 ✅ — Compile + `Task.fields` + assembly task** (layer 5).
  `src/router/compile.py`: `compile_field_plan` groups routings by
  `(bucket, resource, assurance tier)`, caps each group by a token budget, seeds each
  task, picks `single`/`debate` topology by assurance, and fans in to one
  `metadata_generator` assembly task. `Task` gained `fields` / `field_bindings` /
  `topology` (additive, optional — the source-driven planner and executor are
  unaffected), and `task` stays a short action identifier. **Propose-not-commit:**
  each task carries the router's *ranked* candidate set per field (`field_bindings`,
  the single candidate carrier), so the executor picks the answering candidate
  (recall@k, not precision@1) and assurance travels as provisional — the compiler
  does not bake in the lexical top-1. Unresolved fields get no extraction task but are named for
  assembly so the record nulls them. Tests: `tests/test_compile.py`. The
  verify/reconcile pass (linchpin 2), and making the assembly's field→finding map
  structural, are M5; wiring the strategy behind an orchestrator flag is M6.
- **M5 — Assurance axis + verify reconciliation** (layer 6, provenance). Closes
  linchpin 2.
- **M6 — Diff against source-driven on sample datasets; flip the default.**

## Test fixture ✅

A purpose-built bundle exercises every hard case above. It lives at
`data/tests/router_test/` and pairs with the `field_router_test` standard in
`src/standards.py`. The "answer key" is kept **here, in the docs**, not in the
bundle, so the router cannot ingest it.

**Bundle:**

- `observations.csv` — 200 rows, opaque columns `oid, la, lo, dt, sp, n, tmp, elv`.
- `codebook.csv` — data dictionary mapping each opaque column to a label + units.
- `README.md` — the narrative source (title, abstract, methods, licence, …).

**Expected fills, by routing bucket:**

| Field | Bucket | Source | Expected |
|---|---|---|---|
| `title`, `abstract`, `methodology`, `creator`, `funding`, `license` | narrative | README.md | quoted from prose (`license` = CC BY 4.0) |
| `record_count` | structural | observations.csv | **200** (see conflict below) |
| `temporal_coverage` | ambiguous-structural | `dt` column / README | 2019-03-11 … 2021-09-30 (README says "March 2019 to September 2021") |
| `spatial_coverage` | ambiguous-structural | `la`/`lo` via codebook | ~ min_lat 53.22, max_lat 54.79, min_lon −10.20, max_lon −8.61 |
| `variables` | ambiguous-structural | codebook.csv | the measured columns explained (`sp` → species codes, etc.) |
| `temperature_units` | ambiguous-structural | codebook vs values | **conflict below** |

**Planted traps:**

1. **Semantic gap.** Columns are opaque (`la`, `lo`, `tmp`). No value survives
   without the codebook link. Tests catalog resolution (layer 3).
2. **Lat/lon undisambiguated by value.** Both `la` (53–55) and `lo` (−10–−9) fall
   in [−90, 90], so the value-prior alone cannot say which is latitude. The
   codebook must. Tests that linking beats naive range-checking.
3. **Record-count conflict.** README asserts "1,000 observations"; the CSV has
   **200**. Tests conflict surfacing (computed count should win over the prose
   claim, with both cited).
4. **Stale-units conflict.** `codebook.csv` says `tmp` is in **Kelvin**; values
   are 4–22, unambiguously **Celsius**. Tests the text-link-vs-value cross-check —
   the resolver should flag the disagreement, not trust the codebook blindly.
