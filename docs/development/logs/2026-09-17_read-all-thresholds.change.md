# Change log 2026-09-17 — With judges on, BM25 filters nothing; every threshold lives in one place

**Goal:** Two things.

1. **Take BM25 out of decisions the judges make.** With the column matcher and passage
   reader on, BM25 still chose which passages each field was read against — top `k`,
   zero scores dropped — so a passage stating a field in words its description does not
   use was never read for it. It saved almost nothing, since each passage is already read
   once for many fields. In the column tier it decided nothing but was still stored on
   each routing, so `--debug` showed a candidate list the matcher never used.
2. **Stop hiding thresholds in modules.** Passage sizes, catalog budgets, fields per call
   and the catalog resolver's classification cut-offs were constants scattered over five
   files, changeable only by editing code.

## Change

### Routing with judges

- **`_passages_for`** (`src/router/route.py`): with a reader, every passage is read for
  every unanswered field, in document order, while the bundle has at most
  `router_read_all_max_passages` passages. Past that, BM25 chooses each field's top `k`,
  as before. Without a reader, BM25 is still the router.
- **Column tier:** with a matcher, no BM25 runs. A routing's candidates are the matched
  column(s) or tool only, and `FieldPlan.catalog_shown` records the card refs the matcher
  saw — once, since every field saw the same catalog.
- **`FieldPlan.judged`** says whether judges decided the plan.
- **Eval:** recall@k and the BM25 risk–coverage signals are reported only for runs
  without judges. With them nothing is filtered, so recall would be 100% by construction.

### `src/thresholds.py`

One frozen dataclass, `Thresholds`, with every tunable number, its stage, a label and a
help text. A value comes from, in order: a `thresholds.use(...)` override for the current
run; `THRESHOLD_<NAME>` in the environment or `.env`; the default. Code reads
`thresholds.current()` when it needs a value, never at import.

| Stage | Threshold | Default | Was |
|---|---|---|---|
| Catalog resolver | `catalog_profile_sample` | 1000 | `_PROFILE_SAMPLE` |
| | `catalog_dictionary_key_precision` | 0.5 | `_DICTIONARY_KEY_PRECISION` |
| | `catalog_dictionary_key_uniqueness` | 0.9 | `_DICTIONARY_KEY_UNIQUENESS` |
| | `catalog_text_codebook_min_entries` | 3 | `_TEXT_CODEBOOK_MIN_ENTRIES` |
| | `catalog_text_definition_max_chars` | 160 | `_TEXT_DEFINITION_MAX_CHARS` |
| | `catalog_whole_doc_max_chars` | 20 000 | `_WHOLE_DOC_MAX_CHARS` |
| | `catalog_prose_read_k` | 3 | `_PROSE_READ_K` |
| | `catalog_passage_max_chars` | 20 000 | `_PASSAGE_MAX_CHARS` (catalog) |
| | `catalog_grounding_support` | 0.5 | inline in `_grounding_grade` |
| Field router | `router_passage_max_chars` | 20 000 | `_PASSAGE_MAX_CHARS` (route) |
| | `router_read_all_max_passages` | 10 | *new* |
| | `router_match_max_chars` | 30 000 | `MATCH_MAX_CHARS` |
| | `router_max_fields_per_call` | 20 | `MAX_FIELDS_PER_CALL` |
| Plan compiler | `compile_task_budget_chars` | 2000 | `_DEFAULT_BUDGET` |

Parameters that used a constant as their default (`k` in `_batch_prose_reads`,
`max_chars` / `max_fields` on the judges, `budget` in `compile_field_plan`) now default to
`None` and read the threshold at call time, so an override reaches objects built before it.

**GUI:** the settings panel shows each stage's thresholds in its module tab and in the
overview, starting from the environment's values. `PipelineSettings.thresholds` joins the
settings token (so cached results follow them) and `environment()` (so the generation
subprocess gets them). In-process module runs apply them with `thresholds.use`, scoped to
the run rather than the process environment.

Not moved: `--candidates` (`k`) was already a flag and a panel setting.

## Measured (`sharetrait_basic_no_trait__TRADAT031`, now 26 fields, all readmes, 8 workers)

| | |
|---|---|
| matcher calls | 2 (13 + 13 fields) |
| reader calls | 8 (4 passages × 2, 12 fields each) |
| wall time | 25 s |
| precision@1 | 8/11 |
| over-answered | 1/15 |
| accuracy | 22/26 (85%) |
| cited correctly | 6/7 |

Remaining misses are not retrieval: `genus_name` is answered from the species sentence,
and `title_dataset` cites `readme` where the label says `readme_hard`.
