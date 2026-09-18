# Logs

Dated records, newest first. A log is a snapshot of one day's work and is not
revised afterwards — where it disagrees with a [plan](../plans/index.md), the
plan is current. `*.change.md` records a change that landed; `*.analysis.md`
records findings from an investigation that changed no code.

## 2026-09

- -
  **[2026-09-18 — Every single-call LLM role is registered in one place, and every prompt is published](2026-09-18_llm-role-registry.change.md)**
  — `src/llm_roles.py` names the five roles with their model setting, referee and prompt;
  the generated prompt reference publishes each one.
- -
  **[2026-09-18 — The router page's table follows what decided the routing](2026-09-18_router-answers-table.change.md)**
  — a judged plan reads one row per field: the answer, its evidence, the judge's reason and
  what to check, with each field's working an expander below. Unjudged keeps the scores.
- -
  **[2026-09-17 — The catalog resolver reads every passage of a document with few enough](2026-09-17_catalog-read-all.change.md)**
  — the size rule gives way to the router's passage-count rule, default 1: reading every
  passage of `readme_long` added no coverage and made several reads' units worse.
- -
  **[2026-09-17 — A document routing lists the sentences that answer the field, and nothing else](2026-09-17_document-answers.change.md)**
  — passages that state nothing are dropped, each located quote is a candidate, unanswered
  fields list nothing; the multi-quote prompt costs `oxygen` on TRADAT031.
- -
  **[2026-09-17 — Tools are matched apart from columns, then bound to the columns they run on](2026-09-17_tool-column-split.change.md)**
  — a tool matcher (cached across bundles) picks the operation; the column matcher picks
  its table or columns; code joins them. Column cards list a column's few distinct values.
- -
  **[2026-09-17 — With judges on, BM25 filters nothing; every threshold lives in one place](2026-09-17_read-all-thresholds.change.md)**
  — the passage reader reads every passage for every unanswered field; `src/thresholds.py`
  gathers 14 thresholds, overridable by `THRESHOLD_<NAME>` and the settings panel.
- -
  **[2026-09-17 — Type and unit fit lowers confidence; it no longer removes candidates](2026-09-17_type-fit-grade.change.md)**
  — the veto becomes a grade that caps assurance at `low`; every field sees one catalog,
  so the column matcher takes 2 calls instead of 5, and splits are even.
- -
  **[2026-09-17 — The candidate judge splits into a column matcher and a passage reader](2026-09-17_column-matcher-passage-reader.change.md)**
  — columns are matched against the whole catalog (same-meaning columns merged, split
  under a budget); each passage is read once for all its fields. 22 judge calls → 13.
- -
  **[2026-09-16 — The document tier offers whole passages, not its best three chunks](2026-09-16_document-passages.change.md)**
  — retrieval becomes an ordering rather than a filter, gated on a judge that can refuse;
  recall@5 5/12 → 12/12. Records why chunk enrichment was rejected and embeddings deferred.
- -
  **[2026-09-16 — A document routing carries the passage it cited, not the file's name](2026-09-16_span-level-routing.change.md)**
  — the judge was shown a 200-character preview of a 2 000-character chunk and abstained
  for want of withheld evidence; it now reads the whole passage, its quote rides on the
  routing, and the sheet grades the passage rather than the file.
- -
  **[2026-09-15 — Claims agree by meaning, judged by a claim comparer](2026-09-15_claim-comparer.change.md)**
  — `LLMClaimComparer` groups differing claims by meaning in one call per bundle;
  corroboration and conflict follow the groups, and each column is decided once.
- -
  **[2026-09-15 — A long document is read in packed passages, not chunk by chunk](2026-09-15_packed-prose-reads.change.md)**
  — retrieved chunks are packed into passages of up to 20 000 characters, one LLM call
  each; `readme_long.txt` goes from 19 calls to 2.
- -
  **[2026-09-14 — The field reader is the LLM candidate judge](2026-09-14_candidate-judge.change.md)**
  — layer 4b renamed for what it does: judge whether any routed candidate answers a
  field, including that none does.
- -
  **[2026-09-14 — The router routes a resolution; it does not resolve](2026-09-14_router-handoff.change.md)**
  — `ResolvedBundle` hands a catalog and its source files from layer 3 to layer 4; the
  router page is gated on a catalog run and routes it in memory.
- -
  **[2026-09-14 — A glossary is accepted on its structure, not its separators](2026-09-14_text-codebook.change.md)**
  — the glossary regex and `DeterministicProseReader` replaced by a text codebook: runs of
  entries keyed on the schema, ranked below a codebook table; isolated matches go to the
  reader.
- -
  **[2026-09-11 — One settings panel for the whole pipeline](2026-09-11_pipeline-settings.change.md)**
  — every stage's parameters on the landing page; configuration read when it is
  asked for rather than at import; the field router gained the LLM prose reader.
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

2026-09-18_llm-role-registry.change
2026-09-18_router-answers-table.change
2026-09-17_catalog-read-all.change
2026-09-17_document-answers.change
2026-09-17_tool-column-split.change
2026-09-17_read-all-thresholds.change
2026-09-17_type-fit-grade.change
2026-09-17_column-matcher-passage-reader.change
2026-09-16_document-passages.change
2026-09-16_span-level-routing.change
2026-09-15_claim-comparer.change
2026-09-15_packed-prose-reads.change
2026-09-14_candidate-judge.change
2026-09-14_router-handoff.change
2026-09-14_text-codebook.change
2026-09-11_pipeline-settings.change
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
