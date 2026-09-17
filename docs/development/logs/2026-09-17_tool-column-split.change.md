# Change log 2026-09-17 — Tools are matched apart from columns, then bound to the columns they run on

**Goal:** Two things.

1. **A tool is an operation, not a place.** Tool cards sat in the column matcher's
   catalog and competed with columns. That hid two problems. A tool's answer does not
   depend on the bundle, yet it was asked again for every bundle. And a tool that needs
   a column — a date range over a date column, a bounding box over latitude and
   longitude — could not be offered at all, because nothing chose its column.
2. **A column holding one repeated value is that value**, but its card could not say
   so: the profile kept a numeric range and nothing for text.

## Change

### Tool matcher (`src/router/tool_matcher.py`)

- Fields × tool cards, with nothing from the bundle. A card says what the tool computes
  and, under `needs columns`, what each column it needs must hold.
- Its answers are cached on disk, keyed by a hash of the model and the prompt, so a
  standard costs a call on its first bundle and none after. `examples/field_router_plan.py`
  and `python -m eval` keep them in `.cache/tool_matcher` (gitignored).
  `--refresh-tool-cache` (the router page's "Refresh tool cache", and "Ask the tool
  matcher again" in the settings panel's Field router tab) asks again and saves the new
  answers in their place. A failed call is not cached.

### Tool arguments

- `@context_tool(..., column_args={name: ColumnArg(holds, values)})`. A field-answering
  tool must declare every required argument the runner cannot supply; otherwise
  registration raises `TypeError`.
- `get_temporal_extent` (`time_column`, dates) and `get_spatial_extent` (`lat_column`,
  `lon_column`, numbers) are now field-answering.
- For each tool chosen, the router adds one line per argument to the column matcher's
  field list: `measure_date[time_column] (column)`. A tool that takes only a table gets
  `sample_size[resource] (table)`, and one card per table is added to the catalog —
  only when such a line exists.

### Joining the answers — code, not a model (`_bind`, `_settle_structured` in `route.py`)

| Case | Result |
|---|---|
| every argument answered | tool routing; `tool_arguments` holds one argument set per table it runs on |
| an argument unanswered, or its columns not in one table | tool dropped, `tool_note` says why; the field falls back to its column pick, then the documents |
| an argument column's values do not fit (`argument_mismatch`) | kept; assurance capped at `low` |
| the field's own column pick is one of the tool's columns | one tool routing |
| the field's own column pick is a different column | both kept, column first; assurance at most `medium` |

- A tool matcher without a column matcher raises `ValueError`. Without judges, tools that
  need columns are left out of BM25 ranking.
- Compiler: a tool task opens the tables its tool was bound to (still all of the context
  for a tool bound to none); `field_bindings` carry `tool_arguments`.

### Distinct values

- `ResolvedColumn.distinct_count` and `distinct_values`, counted over the profiled rows.
  Values are listed up to the new threshold `catalog_distinct_values_max` (5).
- A column card shows `distinct_values`, or `distinct_count` when there are too many. The
  column matcher's prompt says a single value holds for every record, and to lower its
  confidence when a single-value field gets a varying column unless the field asks for a
  summary.
- `FieldRouting.varies` records a column with more than one value routed to a field typed
  as one value. **It is a note, not a cap.** It was planned as a cap at `low`, but that
  demoted correct routings in the existing tests — `min_latitude` from `lat`,
  `water_temperature` "measured at each station" — because a type cannot tell "one value"
  from "a summary of the values". The matcher sees the values and can.

### Smaller

- `FieldPlan.tools_shown`; `FieldRouting.tool_choice`, `tool_note`, `tool_arguments`,
  `varies`.
- `--debug` prints the tool choice, its arguments and the varies notes.
- `build_judges` returns a `Judges`; `build_plan(..., judges=)`.

## Measured (`sharetrait_basic_no_trait__TRADAT031`, 26 fields, all readmes, 8 workers)

| | before | after |
|---|---|---|
| tool matcher calls | — | 2 on the first run, 0 on the second (cached) |
| column matcher calls | 2 (13 + 13 fields) | 2 (14 + 13: 26 fields + `sample_size[resource]`) |
| passage reader calls | 8 | 8 |
| routing time | 25 s | 25 s (second run) |
| precision@1 | 8/11 | 8/11 |
| over-answered | 1/15 | 1/15 |
| accuracy | 22/26 | 22/26 |
| cited correctly | 6/7 | 6/7 |

- `sample_size` → `get_item_count`, bound to `Blood nitrate_nitrite` only. The label's
  note says the row count of each table; a `resource` line takes one table, or all the
  tables of a merged column if the matcher names a column.
- No other field was given a tool. `pH` routes to the six `pH` columns with a varies
  note (2 values each).
- The first run took 429 s end to end for the same column and reader calls the second
  run made in 25 s; the difference was the endpoint, not the call count.
