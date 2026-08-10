# Change log 2026-08-04 — Field router M4: compile the FieldPlan into a Plan

**Goal:** Land milestone 4 of the field-driven router
([plan_field_router.md](plan_field_router.md), layer 5) — the step that turns the
routing *artifact* into the execution *form*. M3 decided **where** each field is
answered; M4 lays out the work so the existing executor can run it, without
changing anything downstream. `compile_field_plan(field_plan) -> Plan` emits the
same `Plan`/`Task` shape the source-driven planner does, so `PlanExecutor` and
`StepExecutor` run unchanged — the field-driven path is an alternative front-end,
not a fork.

## Linchpin 1 — field identity survives compilation

The whole milestone rests on one invariant: the `FieldPlan` is the source of
truth, a `Task` is only its execution form, and the field → task → result
correspondence must be carried **explicitly** — never reconstructed by heuristics
afterwards, or we are back to the post-hoc matching the router was built to
replace. So `Task` gains three additive, optional attributes
([src/core/schemas.py](../../src/core/schemas.py)):

```
Task{ …, fields: [str], candidates: [dict], field_bindings: [dict], topology: str|None }
```

- **`fields`** — the schema paths this task is responsible for filling. This is the
  linchpin: a produced value maps back to the field it answers by construction.
- **`field_bindings`** — the authoritative per-field binding: for each field,
  `{field, query, assurance, candidates}` where `candidates` is that field's *ranked*
  candidate set. Carries field → candidate *structurally* (not in prose), and is
  what makes selection deferrable — see the revision section below.
- **`candidates`** — the deduped union of the above: a compact overview of the task's
  context slice. *Retrieval is the curation.*
- **`topology`** — a per-task hint (`single` / `debate`) chosen from assurance.

All four default empty/None, and the executor reads none of them yet, so the
source-driven planner and the current execution path are entirely unaffected —
both planners still emit one `Task` shape.

## What the compiler does — and only that

[src/router/compile.py](../../src/router/compile.py). Deterministic and LLM-free;
it lays out execution from the routing artifact:

- **Groups** routings that share an extractor *and an assurance tier* — the same
  `(bucket, resource, tier)` — into one task, so fields answered from the same place
  are extracted together instead of one survey per field. Structural fields
  (whole-resource tools) share one group; column and span fields group by the
  resource that holds them.
- **Caps each group by a token budget** (`_DEFAULT_BUDGET`, char-estimated over the
  seeded snippets). Grouping risks re-introducing the flood at the group level, so a
  group whose candidates exceed the budget is split into several tasks; a single
  field larger than the budget still gets its own task, so nothing is dropped.
- **Seeds each task** with just its group's candidates, deduplicated by
  `(resource, locator, kind)`.
- **Proposes a set per field; it does not commit** (see the revision below): each
  task carries per-field *ranked* candidate sets and a select-and-extract
  instruction, so the executor picks which candidate actually answers each field.
- **Chooses topology per task from assurance**: `single` for high-assurance fields
  (the computation is its own check), `debate` otherwise — the debate machinery's
  best real use is resolving disagreement between grounded sources, not re-surveying.
- **Fans in to one assembly task** (`metadata_generator`) that depends on every
  extraction output and emits `final_metadata`, replacing the monolithic synthesis.

### Tiering keeps debate targeted (a fix caught by the realistic tests)

The first cut grouped only by `(bucket, resource)` and picked `single` for a task
only when *every* field in it was high-assurance. On a realistic bundle that
misfired: a contested column (the `water_temp` Kelvin trap → low assurance) shared
its resource with two perfectly computable coordinate fields, so all three landed
in one task and the whole task went to `debate` — dragging two high-assurance
fields into a multi-player debate they did not need. That is exactly the "topology
overkill" risk the plan names, and it violates "debate *only* for
contested/low-assurance ones." The fix makes the **assurance tier part of the group
key**: high-assurance fields form their own `single` task, contested ones their own
`debate` task, even when they come from the same resource. (Output-artifact names
are now numbered by a single global index, since two groups can share a
`(bucket, resource)` and differ only in tier.)

## Revision: the compiler proposes a set, it does not commit

**The flaw.** The first cut bound each field to its top-ranked candidate
(`candidates[0]`) and wrote that single locator into the instruction as a command
(`title <- (430, 517)`). Reviewing a compiled plan exposed the problem: on the
one-long-README bundle, `title` was *commanded* to the Licence span — the router's
lexical top hit — even though the correct title span (`# Coastal Rockpool Survey
2021`) sat right there in the candidate pool, merely ranked second. Two costs
followed, and both are structural, not cosmetic:

1. **It made the router the decider, not the proposer** — a direct violation of
   linchpin 2, *"the router proposes; the evidence disposes."* A deterministic,
   lexical top-1 pick was baked into the plan with no recourse, so the whole
   pipeline's correctness rested on the router achieving **precision@1** — which BM25
   without stemming cannot (`title`, `record_count`→`oid`, …).
2. **The field → candidate binding lived only in prose.** `fields` was a flat list
   and `candidates` a flat *deduped* pool; which candidate answered which field
   existed only inside the instruction sentence. The verify pass (M5) would have had
   to parse English to trace a value to its source, and a candidate shared by two
   fields carried a score from whichever field was seen first.

**The fix — defer selection to the executor, keep planning deterministic.** The LLM
that should choose among candidates is already downstream (the player); the bug was
the compiler *pre-empting* it. So the compiler now emits, per field, the router's
**ranked candidate set** (`field_bindings`) and a **select-and-extract** instruction
that presents those candidates as *options*, not a command. The executor selects
which candidate actually answers each field (or reports none); the router only
proposes. Consequences:

- **Recall, not precision@1.** The router no longer has to *rank* the right
  candidate first — only get it into the top-k. A far weaker, far more achievable
  bar for a lexical scorer, and exactly what "retrieval is the curation" was meant to
  mean. The `title` H1 that BM25 under-ranked is now reachable by the executor.
- **The binding is structural.** `field_bindings` carries `{field, query, assurance,
  candidates}` per field, so the verifier reads the field → candidate map as data,
  and each field keeps its *own* candidate ranking and scores (no shared-pool score
  confusion).
- **Assurance is provisional.** The grade on a binding is the router's proposal,
  carried for the verify pass to confirm or revise once the executor has selected —
  not a settled fact.

The compiler stays fully deterministic and LLM-free: it lays out *which* fields,
grouped how, for which player, with which candidate options — and defers the one
judgment BM25 is bad at (semantic selection among candidates) to the LLM already
present at execution. Grouping still uses the provisional assurance to pick topology,
so the tiering fix above is unaffected.

Still open (M5): the assembly task hands the synthesizer the findings and the field
list but not an explicit field → finding map; that correlation is recoverable from
the plan (each task carries its `fields`) but is not yet handed to the assembler.
Making it structural is part of the verify/reconcile milestone.

## Coverage honored at execution time

An **unresolved** field gets *no* extraction task — nothing can answer it — but it
is still named in the assembly task's `fields`, so the record carries an explicit
null instead of a fabricated value. The coverage the router computed before
extraction is now honored *in* execution: the plan simply contains no work for
what could not be routed.

## Scope — what M4 is not

The reconcile/verify pass (linchpin 2 — confirm a produced value actually traces
to its routed candidate, downgrade to `unverifiable` on mismatch) is **M5**. M4
only lays out the work; `attribute_field` as the router's verifier is next. Live
end-to-end execution through `PlanExecutor` with players is exercised structurally
here (a well-formed `Plan`), not run against an LLM — wiring the field-driven
strategy behind an orchestrator flag and diffing it against the source-driven
planner is M6. The compiler's output is deliberately *ready* for the executor
without asking it to change.

## Verification

- `pytest` — **179 pass, 1 skip** (was 151; +28 M4 tests, including the
  propose-not-commit revision). The one unrelated pre-existing `test_connections`
  (`SurfConnection`) failure is not a regression.
  Also added `[tool.pytest.ini_options] testpaths = ["tests"]` to `pyproject.toml`,
  so collection no longer walks the gitignored `docs/_build/` (a Sphinx build had
  dropped a downloadable copy of `test_compile.py` there, colliding on basename).
- New tests: [tests/test_compile.py](../../tests/test_compile.py). Rather than one
  happy path, they drive the compiler with realistic, messy bundles and assert the
  *invariants* via a shared `assert_well_formed` check reused across classes:
  - **`RealisticCompileTest`** — one data table + a codebook with the `water_temp`
    Kelvin trap, **one long multi-section README** (the usual shape — every narrative
    field in its own section of *one* document, not a file per field), a structural
    field, and an unresolvable one. Asserts the plan is well-formed; the long
    document collapses to **one** narrative task (grouped by resource); the router
    **localizes distinct, non-overlapping spans within that single document**
    (`abstract`/`methodology`/`funding` land on their own sections — `title` is
    excluded as the documented low-signal case); a tight budget splits that
    single-document group without any piece leaving the document; **the contested
    column does not drag its high-assurance siblings into debate** (the tiering fix
    above); the structural field is context-level and `single`; a narrative task
    seeds a `quoted_span`, not a column; determinism.
  - **`MultiDocumentTest`** — the other real shape: narrative fields split across
    *separate* files (README + LICENSE). Asserts the compiler groups by resource
    into one task per answering document, and `license` routes to its own file.
  - **`BudgetTest`** — a generous budget keeps a same-tier column group together; a
    1-char budget splits it one-field-per-task and drops nothing; a *derived* budget
    (the cost of the first two fields) forces a real split and the grouped task's
    seeded payload stays within budget; a field larger than the budget still gets
    its own task.
  - **`DegenerateTest`** — an all-unresolved schema yields *only* the assembly task
    (empty fan-in, every field still named for nulling); a single-field bundle is
    valid.
  - **`OverrideTest`** — `bucket_player` / `assembly_player` overrides are honored,
    unspecified buckets keep their defaults.

  The invariant check covers linchpin 1 (every routed field on exactly one task, no
  field twice), assembly naming *every* schema field in order, the fan-in bijection,
  **executable dataflow** (every assembly input produced by an earlier step), the
  bucket→player mapping, homogeneous per-task tier, candidate dedup, and — for the
  revision — that every field has exactly one structured binding with its own ranked
  candidates and a provisional assurance, and that the instruction defers selection.
- Tests for the propose-not-commit revision: the `title` binding **preserves the
  under-ranked-but-correct H1 span** (recall, not precision@1 — the executor can
  recover what BM25 mis-ranked); a binding's `assurance` is the router's grade,
  carried as provisional; and per-field scores are that field's *own* ranking, not a
  shared-pool artifact.
- Demonstrated on `data/tests/router_test/`: the 11-field standard routes 11/11 and
  compiles to **5 tasks** — the six narrative fields → one `metadata_specialist`
  task (seeded spans, `debate`); the high-assurance column fields
  (`record_count`, `spatial_coverage`, `temporal_coverage`) → one `data_analyst`
  `single` task; `variables` → its own `single` task via `get_field_names`; the
  contested `temperature_units` (Kelvin trap) → its **own** `data_analyst` `debate`
  task — the tiering fix visible on the fixture, keeping the high-assurance columns
  out of its debate; and a `metadata_generator` assembly fanning in all four. The
  `record_count → oid` grouping is the pre-existing, documented M3 lexical-ceiling
  mis-route carried faithfully, not introduced here.