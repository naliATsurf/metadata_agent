# Change log 2026-09-14 — The field reader is the LLM candidate judge

**Goal:** Name layer 4b for what it does. "Field reader" suggested it reads a field's
value, which the extractor does later. It also sounded like the catalog's prose reader,
which really does read text. "Reranker" undersold it too: its most valuable answer is
that *no* candidate answers the field, and a reranker always leaves something on top.
It judges whether any candidate answers the field, and may rule none does.

## Renames

| Before | After |
| --- | --- |
| `src/router/rerank.py` | `src/router/judge.py` |
| `FieldReader`, `LLMFieldReader` | `CandidateJudge`, `LLMCandidateJudge` |
| `rerank(candidates, verdict)` | `promote(candidates, verdict)` — it moves the choice to rank 1 |
| `route_fields(..., reader=)` | `route_fields(..., judge=)` |
| `FieldRouting.reader_choice` / `reader_note` / `reader_grounded` | `judge_choice` / `judge_note` / `judge_grounded` (also in the saved field plan) |
| `--field-reader`, `--reader-workers`, `--no-reader-batch` | `--llm-candidate-judge`, `--judge-workers`, `--no-judge-batch` (router example and `python -m eval`) |
| `build_field_reader` | `build_candidate_judge` |
| LLM module `FIELD_READER` → `LLM_{PROVIDER,MODEL,TEMPERATURE}_FIELD_READER` | `CANDIDATE_JUDGE` → `LLM_{PROVIDER,MODEL,TEMPERATURE}_CANDIDATE_JUDGE` |
| `PipelineSettings.router_field_reader` / `router_reader_workers` / `router_reader_batch` | `router_candidate_judge` / `router_judge_workers` / `router_judge_batch` |
| eval `abstained_by="reader"` | `abstained_by="judge"` |
| `tests/test_rerank.py` | `tests/test_judge.py` |

In the demo, the checkbox reads **LLM candidate judge**, and its settings group is
**LLM candidate judge model**. `Verdict`, `candidate_ref`, `describe` and `weaker` keep
their names. The catalog's prose reader is untouched.

An `.env` that sets `LLM_*_FIELD_READER` must rename those variables; the old names are
no longer read. A field plan saved before this change carries the old `reader_*` keys.
