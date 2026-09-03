# Logs

Dated records, newest first. A log is a snapshot of one day's work and is not
revised afterwards — where it disagrees with a [plan](../plans/index.md), the
plan is current. `*.change.md` records a change that landed; `*.analysis.md`
records findings from an investigation that changed no code.

## 2026-09

- -
  **[2026-09-03 — The FieldPlan defers a choice its consumers make anyway](2026-09-03_premature-commitment.analysis.md)**
  *(analysis)* — the bucket, the task's resource, and the assurance are all read
  off rank 1, while the artifact presents the choice as the executor's. Findings
  only; raises an M5 design question.
- -
  **[2026-09-01 — Router UI, bundle discovery, and the routing vocabulary](2026-09-01_router-ui-and-vocabulary.change.md)**
  — a page for the field router; bundle classification moved into the library;
  routing buckets renamed.

## 2026-08

- -
  **[2026-08-31 — Demo: module pages over the example scripts](2026-08-31_demo-module-pages.change.md)**
  — the mechanism for turning any `examples/` script into a GUI page, and the
  catalog resolver page built on it.
- -
  **[2026-08-04 — Field router M4: compile the FieldPlan into a Plan](2026-08-04_field-router-m4.change.md)**
  — `compile_field_plan` emits the `Plan`/`Task` shape the existing executor
  already runs.

## 2026-07

- -
  **[2026-07-22 — Field router M3: the router and the FieldPlan](2026-07-22_field-router-m3.change.md)**
  — routing each schema field to a source, with a coverage report for the fields
  nothing can answer.
- -
  **[2026-07-22 — Field router M2: catalog resolution (symbol linking)](2026-07-22_field-router-m2.change.md)**
  — the pre-routing pass that closes the semantic gap M1 exposed.
- -
  **[2026-07-22 — Field router M1: schema walker, `Searchable`, BM25 search](2026-07-22_field-router-m1.change.md)**
  — the two foundation layers, testable with no planner change.
- -
  **[2026-07-22 — Evidence caller attribution; player prompt cleanup; survey feed dedup](2026-07-22_evidence-caller-attribution.change.md)**
  — three changes prompted by reading the `evidence_*.json` output.
- -
  **[2026-07-20 — Generated prompt reference; prompt de-duplication](2026-07-20_generated-prompt-reference.change.md)**
  — a docs page generated from the live prompts, replacing the copies that had
  drifted.
- -
  **[2026-07-16 — Provenance traces for tabular input](2026-07-16_provenance-traces.change.md)**
  — an evidence ledger plus deterministic attribution, so provenance is computed
  rather than claimed.
- -
  **[2026-07-14 — End-to-end probe against a real deposit](2026-07-14_end-to-end-probe.analysis.md)**
  *(analysis)* — what it would take to process `TRADAT009.zip` against the
  ShareTrait schema. Findings only; motivated the
  [multi-modality reshape](../plans/multi-modality.md).
- -
  **[2026-07-09 — Capability-based tool registry](2026-07-09_capability-tool-registry.change.md)**
  — tools declare the capability they need instead of the formats they are known
  to work on.
- -
  **[2026-07-09 — Tool surface audit](2026-07-09_tool-surface-audit.analysis.md)**
  *(analysis)* — inventory of `context_tools.py`; found two silent failures,
  both traced to one cause and fixed the same day.
- -
  **[2026-07-08 — Context layer refactor and new `TextContext`](2026-07-08_context-layer-refactor.change.md)**
  — splitting `ExecutionContext` into a modality-agnostic base and
  `TabularContext`.

```{toctree}
:maxdepth: 1
:hidden:

2026-09-03_premature-commitment.analysis
2026-09-01_router-ui-and-vocabulary.change
2026-08-31_demo-module-pages.change
2026-08-04_field-router-m4.change
2026-07-22_field-router-m3.change
2026-07-22_field-router-m2.change
2026-07-22_field-router-m1.change
2026-07-22_evidence-caller-attribution.change
2026-07-20_generated-prompt-reference.change
2026-07-16_provenance-traces.change
2026-07-14_end-to-end-probe.analysis
2026-07-09_capability-tool-registry.change
2026-07-09_tool-surface-audit.analysis
2026-07-08_context-layer-refactor.change
```
