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

### Not every field is searched

Scope bound: fields fall into three buckets, and only two need search.

1. **Structurally determined** → direct tool binding, no search. `row_count` is
   *always* `get_item_count`; `columns` is *always* `get_field_names`.
2. **Ambiguous-structural** → the *schema router* ("which column is the date?").
3. **Narrative** → *document retrieval* (`abstract`, `license`, `methodology`).

## Data model 🔲

Four new types; the last is the persisted deliverable.

```
FieldSpec       { path, description, type, required }          # flattened from the schema
EvidenceRef     { resource, locator, kind, snippet, score }    # a candidate; locator = span | column
FieldRouting    { field_path, query, candidates:[EvidenceRef],
                  assurance, extractor_role, topology }
FieldPlan       { routings: {field_path: FieldRouting}, schema_ref }   # source of truth
```

`kind` on `EvidenceRef` is the assurance carrier: `computed_column` >
`verified_span` > `quoted_span`.

## The pipeline — five layers 🔲

Ordered so each is testable before the next and the migration stays incremental.

1. **Schema walker → `FieldSpec[]`.** Flatten the output schema to leaf fields
   with descriptions, handling nested/optional/union Pydantic models (naive
   "iterate the fields" fails on nesting). Pure function.
2. **`Searchable` capability + `search_context` tool.** One capability class
   (gated by `requires=Searchable`, like `TabularContext`), one
   `@context_tool`. Because it funnels through the tool boundary, retrievals land
   in the evidence ledger with `used_by` (new `phase="route"`) for free, and
   identical queries dedupe through `_RESULT_CACHE`.
   - `TextContext.search` → semantic retrieval over prose → spans.
   - `TabularContext.search` → field-to-column matching over the identifier
     catalog → column pointers (generalizing `detect_*_columns`).
3. **Router → `FieldPlan`.** For each `FieldSpec`, pick a bucket, run `search`
   where needed, produce a `FieldRouting`. Coverage falls out: every field has a
   routing or is flagged `none` → `unverifiable` *before* extraction.
4. **Compile `FieldPlan` → `Plan(List[Task])`.** The linchpin-1 step:
   - Group routings sharing (resource set, extractor) into one `Task`, **capped by
     a token budget** so grouping does not re-introduce flooding at the group
     level.
   - Each `Task` carries `fields=[…]` and seeds the players' workspace with that
     group's `candidates` — no whole-context survey.
   - Topology per task from assurance: single extractor for high-assurance
     computed fields, debate only for contested/low-assurance ones.
   - One terminal **assembly `Task`** depends on all extraction tasks (fan-in),
     replacing the monolithic `metadata_generator` synthesis.
   - `PlanExecutor` / `StepExecutor` then run **unchanged**.
5. **Reconcile + verify (linchpin 2).** Walk the `FieldPlan`; for each field find
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
is contained to layers 1–5 and the swap is reversible.

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

- **M1 — Schema walker + `Searchable`/`search_context`** (layers 1–2). Testable in
  isolation; no planner change yet.
- **M2 — Router + `FieldPlan` + coverage report** (layer 3). Emits the artifact;
  still executes via the old planner for comparison.
- **M3 — Compile + `Task.fields` + assembly task** (layer 4). Field-driven
  execution end to end behind the flag.
- **M4 — Assurance axis + verify reconciliation** (layer 5, provenance). Closes
  linchpin 2.
- **M5 — Diff against source-driven on sample datasets; flip the default.**
