# Change log 2026-07-16 — Provenance traces for tabular input

**Goal:** Ship per-field provenance for tabular extraction runs: for each field
in the produced metadata, record which tool call's output supports the value —
or flag it as unverifiable when nothing does. Two layers: an evidence ledger that
captures every fact a tool produces, and deterministic attribution that traces
each metadata value back to that evidence. Provenance is *computed*, not claimed
by the model, so it is grounded in real tool output and testable without a live
provider. Recording is off the data path; extraction behavior is unchanged.

## One capture site instruments the whole tool surface

Every tool funnels through `_build_llm_facing_function` in `src/tools/base.py` —
it already resolves the context, knows the tool name, and holds the bound
arguments and the result. That single wrapper is where capture belongs:

```python
result = fn(ctx, **bound.arguments)
call_args = {k: v for k, v in bound.arguments.items() if k != "resource"}
record_evidence(
    context_key=key,
    tool=fn.__name__,
    resource=bound.arguments.get("resource"),
    args=call_args,
    result=result,
)
return result
```

- **Universal, tabular, and any future modality are instrumented with no
  per-tool code.** The same lever that already derives dispatch flags at this
  boundary now also records provenance.
- **`resource` is separated from the tool's other arguments**, matching how the
  rest of the system treats it (resource-scoped dispatch, artifact naming).
- **Recording runs after `fn` and is a pure append** — it cannot alter a return
  value, and a tool that raises records nothing (it produced no fact).

## The ledger — `src/provenance.py`

- `EvidenceEntry`: `id`, `context_key`, `tool`, `resource`, `args`, `result`,
  plus `describe()` (a human/LLM citation like
  `get_temporal_extent(measurements, {'time_column': 'date'})`) and `to_dict()`.
- `record_evidence()` → returns an `ev_000001`-style id. `get_evidence(key)`,
  `serialize_evidence(key)` (plain dicts for a prompt or eval consumer),
  `clear_evidence(key=None)`.
- **Keyed by `context_key`.** Each run registers its context under a fresh key
  (`PlanExecutor.execute`), so evidence is namespaced per run *without the ledger
  knowing about run boundaries*, and the final generator — which already holds the
  `context_key` — retrieves exactly its own run's facts. A second run's context
  sees none of the first's.
- **Full arguments are captured, defaults included.** `apply_defaults()` fills a
  tool's defaults (e.g. `get_unique_values`'s `limit=100`) before capture. This is
  deliberate: replay-based verification needs the exact argument set the tool ran
  with, not just the caller-supplied subset.

## Lifecycle

`clear_registry()` now clears the evidence ledger alongside the context registry,
so the two share one cleanup lever and evidence cannot outlive the contexts it
refers to. Per-`context_key` keying means a single run is already self-scoped;
`clear_registry()` covers the long-lived-process case (TUI, demo app).

## Attribution — from evidence to per-field provenance

`src/provenance.py` also does the tracing. `attribute_field(value, entries)`
matches a metadata value against captured tool results, escalating from most to
least specific and taking the first match:

1. the value equals a whole tool result → `verbatim`;
2. the value equals a scalar leaf inside a result → `derived`;
3. the value's string form appears within a result → `derived`.

Anything present but unmatched is `unverifiable` — **the confabulation signal**: a
value the run asserted that nothing it captured supports. A null/empty value is
`not_present`. Matching is conservative by design: a wrong attribution is worse
than none. `attribute_metadata(metadata, entries)` maps this over a record,
yielding a `FieldProvenance` sidecar **parallel to the value dict** (same keys),
each entry carrying `status`, `source_type`, `source_ref` (the citing tool call),
`transform`, `evidence_id`, and a compact self-contained `evidence` snippet.

For tabular input this is the *strong* form of provenance: tool outputs are exact,
reproducible facts, so attribution is grounded in what the tools actually
computed rather than in a model's self-report — and it is fully deterministic.

## Wired into the run — `ExecutionResult.final_provenance`

- New optional `final_provenance` field on `ExecutionResult`
  (`core/schemas.py`), parallel to `final_metadata`.
- Companion `final_evidence` field: every fact the tools produced during the run,
  in order, addressable by `evidence_id`. It is the raw material the sidecar
  attributes to, and it is what makes an `unverifiable` field *interpretable* —
  you can see what the run actually had to work with. Populated for every run
  (evidence is captured regardless of standard), via `serialize_evidence`.
- `PlanExecutor._build_provenance` runs after the metadata is extracted and
  attributes each **standard field** (from `output_schema.model_fields`) to the
  run's evidence. Scoped to schema-backed runs: the schema gives the exact field
  set to trace, so provenance covers the standard's fields, not whatever
  incidental keys a fallback record carries. Runs with no `metadata_standard_name`
  get `final_provenance = None`.
- Deterministic and pure — no LLM call, no network — so it adds no failure mode
  to a run.

## Verification

- `python -m unittest tests.test_provenance` — **14 pass.** Capture: a call;
  resource/args separation (including defaults); id uniqueness and ordering;
  citation rendering; per-`context_key` scoping; a raising tool recording
  nothing; serialization; `clear_registry` clearing evidence. Attribution: whole
  result → verbatim; scalar traced to its producing tool; unsupported value →
  unverifiable; null → not_present; `0` traced as a value, not an absence; sidecar
  keys parallel to the value record.
- `python -m unittest discover tests` — **74 pass, 1 skip** (was 60/1); no
  regression.
- End-to-end: a schema-backed `PlanExecutor` run over an `obs.csv` context, with
  the survey phase populating the ledger, produced `final_provenance` tracing
  `columns → get_field_names(obs)` and `row_count → get_item_count(obs)` as
  `verbatim`, and flagging an invented `title` as `unverifiable`.

## Scope and limits

- **Tabular input, tabular strength.** Attribution is reliable exactly where the
  source is informative: structural fields (extents, counts, field lists, stats)
  trace cleanly to tools. Descriptive fields a model *synthesises* from loose
  reading (a prose `description`) will often not match any single tool output and
  come back `unverifiable` — which is the honest signal, and mirrors the
  "CSVs carry data, not metadata" finding.
- **Not gated to tabular contexts.** `_build_provenance` runs on *every*
  schema-backed run, not only tabular ones; on a text or other context it simply
  marks fields `unverifiable` for want of tool facts to match. Left ungated
  deliberately — the behavior degrades cleanly, and a context-type guard is a
  one-liner to add once the text-provenance story is built. The "tabular input"
  framing is about where the trace is *strong*, not where the code runs.
- **The model still authors the values.** Provenance is computed *after*
  generation, so it grades what the generator produced; it does not yet *force*
  the generator to only assert grounded values. Making an unsourced value
  unrepresentable at generation time (the `TracedField` prompt/validator) remains
  the next increment.
- **No replay verifier yet.** Attribution records which evidence supports a value;
  re-invoking the cited tool to confirm the value still follows lands with the
  auditor. The captured `(tool, resource, args)` is sufficient for it.
- **Investigate-phase capture is correct by construction but unverified against a
  live provider.** Model-chosen tool calls pass through the same wrapper, so they
  are captured, but the test provider has no tool-calling support (the suite's one
  skip), so only the survey path is exercised end to end.
