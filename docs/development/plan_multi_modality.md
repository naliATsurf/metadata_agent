# Reshape Plan: getting the model out of the data path

This document plans the reshape forced by the end-to-end probe against a real
deposit (`TRADAT009.zip` against the ShareTrait schema). It assumes the findings
in the [analysis log](14-07-2026_analysis.log.md) and the layer split in
`data/schema/`. It supersedes nothing in [plan.md](plan.md) — the free-text work
there remains valid and is folded in as Phase 1.

Status legend: ✅ done · 🟡 partial · 🔲 not started · ⛔ blocked on a decision.

## Motivation and approach

The probe found the pipeline cannot process a real deposit against a real
standard. The reason is **not** that the architecture is wrong. Three parts of it
turned out to be already general and survive untouched:

- the **workspace** is `Dict[str, Any]` — an artifact can already be a DataFrame,
  a record list, or a mapping spec;
- the **plan/artifact dataflow** (declared `inputs`/`outputs`, dependency
  validation) does not care what flows through it;
- the **tool layer** (capability gating via `isinstance(ctx, requires)`) is
  entirely orthogonal to output shape.

The single load-bearing defect is that **`Task.player` is required, so every step
is executed by an LLM** (`core/schemas.py`; `PlanExecutor.execute` routes every
step through `create_step_state` → `step_graph`).

**The reshape is one idea: move the model out of the data path and into the
control path.** The model authors a *mapping*; deterministic code applies it. This
is exactly how the ground-truth record was built — the model's output was a
declarative mapping table, and a `for` loop produced the 1,252 rows.

We **extend, not fork**, as in the previous refactor. Work concentrates in
`context/`, `standards.py`, `core/schemas.py`, and two new packages
(`src/resolvers/`, `src/transforms/`).

## The shape of the target

`data/schema/` splits the standard by **scale** — what a table's row count depends
on. The rule is mechanical: a table is observation-scale iff it keys on or
references an observed entity (`individual_pk` / `measurement_pk`).

| Layer | Tables | Attributes | Rows in TRADAT009 | Row count follows |
|---|---|---|---|---|
| `descriptive_metadata.csv` | 15 | **132** | **20** | the experimental design |
| `data_model.csv` | 6 | **63** | **1,232** | the data |

This inverts the intuition and it drives the whole plan:

- **68% of the attributes live in ~20 rows.** Populations, sites, places, taxonomy,
  conditions, traits, the dataset and manuscript. Small, enumerable, reviewable —
  **a model can write these out directly.**
- **The remaining 32% of attributes are 98% of the rows.** Individuals,
  measurements, and their junctions. **A model must never write these**: cost,
  context limits, and above all non-determinism, which is fatal for curation.

So the milestones are not "descriptive vs. data model" but **"what a model may
emit, and what it may not."**

---

## Phase 0 — Two decisions, neither of them code ⛔

Both block Milestone 2. Neither is an engineering task.

- **The schema cannot hold 89% of the deposit.** Repeated measures (1,374 rows),
  group-scoped traits (threshold weight), and cell size have nowhere to go —
  `measurement` is a single-observation model with no time dimension. Decide
  whether ShareTrait grows a timepoint key and a group-measurement table, or
  whether dropping this data is accepted. **Automating a pipeline that faithfully
  drops 89% of the data automates waste.**
- **The deposit count.** A system is an amortization bet. At ~20 deposits the build
  never pays back and the right answer is a curator plus Phases 2, 4 and 5. At 500+
  with ongoing arrivals, *drift* is what kills you — 500 deposits curated by 500
  independent model runs produce 500 dialects — and the system is justified.

---

# Milestone 1 — the study-scale layer

**132 of 195 attributes, ~20 rows, full provenance, zero confabulation.**
No executor change. This is a real end-to-end run on a real deposit, and it
de-risks everything in Milestone 2. It is worth doing **regardless of how Phase 0
resolves** — including under the "hire a curator" outcome, which needs Phases 2,
4 and 5 anyway.

## Phase 1 — Read the whole deposit 🔲

Homogeneity is assumed in three interlocking places, and a mixed deposit hits all
three. `ContextFactory._create_from_list` types the context from `paths[0]`;
`_create_from_directory` globs `*.csv` and **silently drops** everything else;
`classify_context_type` returns `UNKNOWN` on mixed input. `.zip` is not in
`EXTENSION_MAP` at all, so it falls through `context_factory.py:203` into
`CSVContext(zip)` — the system does not degrade on the deposit, it breaks.

- `CompositeContext` holding sub-contexts of different modalities behind one
  **flat resource namespace**. Resolve the *owning sub-context per resource* inside
  `_build_llm_facing_function` (`tools/base.py`), so `isinstance(ctx, requires)`
  stays a real type check. `tools_for(composite)` returns the union for the manifest.
- Unpack archives at the factory boundary.
- **PDF reader.** No PDF dependency exists anywhere in the project today.
- `.R` → `ContextType.TEXT` initially; a `CodeContext` only if it earns its keep.
- **Phase 3 of [plan.md](plan.md) — the `text.*` toolset — is a hard prerequisite.**
  `TextContext` exists and exposes `read_text`/`search`/`iter_chunks`, but no tool
  surfaces any of it, so a text resource sees only the four `universal` tools.

Why this is first: **the CSVs supplied 9 of 139 filled attributes; the two PDFs
supplied 42.** Units, trait definitions, experimental conditions, the collection
site, the species, every date range — all PDF-only. The R script headers yielded
the manuscript title, which is what made the DOI findable. Almost the entire
study-scale layer is locked behind this phase.

## Phase 2 — Authority resolvers 🔲

**26 attributes are unobtainable from the deposit at any cost.** New
`src/resolvers/`: Crossref (manuscript DOI, citation, publisher), Catalogue of Life
(taxon ID, classification, authority), GeoNames (country ID), Dataverse (dataset
DOI, licence, version, dates).

Fully reusable, **zero model involvement**, independently testable. The cheapest
high-leverage piece in the plan, and Milestone 2 depends on it.

These populate the **reference tables** — `ref_taxonomy`, `place`, `trait` — whose
rows are shared *across* deposits. They are where corpus consistency is won or
lost: if two deposits resolve *Lycaena phlaeas* to different taxonomy rows, the
cross-dataset queries the database exists to serve are already broken.

## Phase 3 — Standards that can express records 🔲

`STANDARD_DEFINITIONS` as `Dict[field, spec]` cannot express the study-scale layer
either — `condition` has 7 rows and `trait` has 2, and `ref_taxonomy` needs nested
classification. Flatness bites even in the easy class: DataCite's `creators` is a
list of name/affiliation/ORCID objects.

Grow it to carry **entities, cardinality, primary keys, references, and nesting** —
enough to describe both layers. This is the class of standard that includes Darwin
Core Archive and DDI; it is not a ShareTrait peculiarity.

`ExecutionResult.final_metadata: Optional[Dict[str, Any]]` widens to hold record
collections, and `_extract_final_metadata` stops hunting for a single artifact by
name.

Register `sharetrait` from `data/schema/`.

## Phase 4 — Provenance, and refusing to invent 🔲

`metadata_generator`'s prompt says *"Fill in ALL fields from the standard with
actual values."* Against 195 attributes, 64 mandatory and 26 unobtainable, that is
an instruction to invent DOIs and taxonomy IDs.

Every emitted value must carry a **source**, and an unsourced value must be
*unrepresentable* — not merely discouraged. The ground truth deliberately leaves
the mandatory `place.geoname_countryid` **empty**; it is the single most valuable
cell in the fixture.

## Phase 5 — Policy registry 🔲

~17 attributes are **controlled-vocabulary decisions**, not extractions: growth
rate → `development`; body size → `size_value`; `origin` wanting one term where the
truth is two; `population_label`; the `sharetrait_datasetid` naming convention;
surrogate-key formats. A curator makes these **once**; the system enforces them
**forever**. No model should re-improvise them per deposit — that is precisely how
a corpus acquires 500 dialects.

Key minting belongs here too. A model *could* mint 20 study-scale keys, but it
must not: keys must be stable and deterministic across runs.

---

# Milestone 2 — the observation-scale layer

**63 attributes, 1,232 rows.** Gated on Phase 0. This is the reshape.

## Phase 6 — A step that is not a player 🔲

**The load-bearing change.** Generalize `Task.player` to an `executor` that is
*either* a player role *or* a registered deterministic transform. One field in
`core/schemas.py`, one dispatch branch in `PlanExecutor.execute`.

## Phase 7 — The mapping is the model's output 🔲

A player emits a **Pydantic-validated `column_mapping` artifact** — source column →
target attribute, with a transform (`verbatim` / `derived` / `vocab-map` /
`unit-convert` / `reshape` / `minted`). It never emits rows.

`data/end2end_test/record/attribute_mapping.csv` is a worked example of exactly
this artifact: 195 rows, every attribute accounted for, authored by a model,
applied by code.

## Phase 8 — The transform library 🔲

New `src/transforms/`, mirroring the proven `@context_tool` registry discipline —
but transforms are **not** tools. Tools are model-callable and return *facts about
a context*; transforms are runner-callable and produce *data artifacts*. Separate
registry, same rigor.

- `apply_mapping` — wide→long reshape per the mapping artifact
- `mint_keys` — surrogate keys under the Phase 5 policy
- `resolve_authority` — calls Phase 2
- `check_fk_integrity` — all 16 foreign keys must resolve, or the run fails
- `check_conformance` — **every** dictionary attribute present and accounted for

On that last item: the first ground-truth build was **missing 13 columns** and its
audit reported success, because it checked only *mandatory* attributes and all 13
were optional. **An incomplete check that reports green is worse than no check.**
The same trap is open in `validate_plan_tool_compatibility`, whose failure mode is
silent omission.

## Phase 9 — Topology reform 🔲

Nothing in the probe was won by agent choreography. The MTSP error (the description
PDF says 18°C where the data says 23°C) was caught by **cross-checking two
sources** — a method property, not a topology property.

The reshape finally gives the debate machinery a principled line: **debate the
mapping, never the rows.** Three players arguing whether growth rate belongs under
`development` is a defensible use of a critic. Three players arguing about row 87 of
`measurement` is nonsense. Justify `players_per_step` / `debate_rounds` on judgment
steps only — or delete them.

---

## Phase 10 — Evaluation 🔲 (throughout)

`data/end2end_test/record/` is committed ground truth: 17 tables, 1,252 rows, all
16 FKs resolve, all 195 attributes accounted for in `attribute_mapping.csv`.

Score a run on three axes, **in this priority order**:

1. **Confabulation** — values filled with no source. Target: zero. The empty
   `geoname_countryid` is the canary; a run that fills it convincingly has failed.
2. **Conformance** — every attribute present; all FKs resolve.
3. **Coverage** — attributes correctly filled.

Coverage is the *least* interesting of the three, and optimizing it first is how a
system learns to lie.

## Friction points and notes

- **The model in the data path is the whole bug.** If only one phase of Milestone 2
  is built, build Phase 6.
- **Study-scale does not mean one row per dataset.** TRADAT009 has one population
  and one site because it is a single-colony lab experiment; the dictionary's own
  `table1` example describes *"five Chilean populations along a latitudinal
  gradient"*. The property is *bounded by design*, not *equal to one*.
- **`describe` (table4) is the seam.** Study-scale, but it joins `dataset_pk` to
  `population_pk`, so it must be produced after both exist.
- **Cross-check prose against data.** A reader that trusts the description PDF
  corrupts the `condition` table. Two sources, or none.
- **The system currently loses to a general coding agent with a shell.** What a
  system adds is not intelligence but **consistency of judgment across a corpus,
  policy that lives somewhere, provenance by construction, and amortized cost** —
  every one of those is a Phase 2/4/5 concern, not a Phase 6 one. Phase 0's second
  question asks whether any of it is worth having.

## Order of work

```
Phase 0  ⛔  decide: schema, and N          (gates Milestone 2 only)
   |
Milestone 1  — 132 attrs, ~20 rows, no executor change
   1 read the deposit  →  2 resolvers  →  3 standards  →  4 provenance  →  5 policy
   |                                                     ← start here
Milestone 2  — 63 attrs, 1,232 rows        (needs Phase 0)
   6 code steps  →  7 mapping artifact  →  8 transforms  →  9 topology
        ^
        the one that matters
   |
Phase 10  evaluation, throughout
```

The minimum for a first honest end-to-end run is **Phases 1, 2 and 4**: read the
whole deposit, resolve what the deposit cannot provide, and refuse to invent the
rest. That yields a partial study-scale record with full provenance and zero
confabulation — smaller than the ground truth, and *honest*.
