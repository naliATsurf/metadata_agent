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
Task{ …, fields: [str], candidates: [dict], topology: str|None }
```

- **`fields`** — the schema paths this task is responsible for filling. This is the
  linchpin: a produced value maps back to the field it answers by construction.
- **`candidates`** — the routed evidence (serialized `EvidenceRef`s) seeding the
  task's workspace: the curated slice its fields need, not a whole-context survey.
  *Retrieval is the curation.*
- **`topology`** — a per-task hint (`single` / `debate`) chosen from assurance.

All three default empty/None, and the executor reads none of them yet, so the
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

- `pytest` — **165 pass, 1 skip** (was 151; +14 M4 tests). The one unrelated
  pre-existing `test_connections` (`SurfConnection`) failure is not a regression.
  Also added `[tool.pytest.ini_options] testpaths = ["tests"]` to `pyproject.toml`,
  so collection no longer walks the gitignored `docs/_build/` (a Sphinx build had
  dropped a downloadable copy of `test_compile.py` there, colliding on basename).
- New tests: [tests/test_compile.py](../../tests/test_compile.py). Rather than one
  happy path, they drive the compiler with realistic, messy bundles and assert the
  *invariants* via a shared `assert_well_formed` check reused across classes:
  - **`RealisticCompileTest`** — one data table + a codebook with the `water_temp`
    Kelvin trap, *three separate* narrative documents, a structural field, and an
    unresolvable one. Asserts the plan is well-formed; each document becomes its own
    narrative task; **the contested column does not drag its high-assurance siblings
    into debate** (the tiering fix above); the structural field is context-level and
    `single`; a narrative task seeds a `quoted_span`, not a column; determinism.
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
  bucket→player mapping, homogeneous per-task tier, and candidate dedup. All
  self-contained (build their own bundles).
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