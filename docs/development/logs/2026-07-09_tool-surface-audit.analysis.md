# Analysis log 2026-07-09 — Tool surface audit

**Goal:** Inventory the tools in `context_tools.py`, establish which are
tabular-specific and which are modality-agnostic, and check that the tools the
pipeline *declares* match the tools it can actually *run*. No code changed;
findings only.

**Method:** Read `context_tools.py`, `players/configs.py`, and the tool-firing
loop in `players/player.py`. Then probed all 19 tools against a live single-CSV
context, invoking each exactly as `Player.execute_task` does, and separately
measured `filter_tools_by_context_type` across all four context types.

**Outcome:** Two silent failures, both tracing to one cause. Fixed the same day
by restructuring the tools module — see
[the change log](2026-07-09_capability-tool-registry.change.md). This log
describes the code *as it was before* that change.

## Inventory

19 tools, all taking a `context_key` string resolved through the module-level
registry rather than a context object. They divide by how they reach the data.

**Modality-agnostic (6)** — call `get_context()`, ask only what the base
context contract can answer:

| Tool | Scope |
|---|---|
| `get_context_overview` | context |
| `list_resources` | context |
| `get_context_schema` | context |
| `get_relationships` | context |
| `get_resource_info` | resource |
| `get_item_count` | resource |

`get_item_count` is agnostic because `item_count` lives on the base
`ResourceInfo` — rows for a table, chunks for a document. The modality split
from 2026-07-08 working as designed.

**Tabular-only (12)** — call `_get_tabular_context()`, which raises `TypeError`
on a non-tabular context:

| Group | Tools |
|---|---|
| Field profiling | `get_field_names`, `get_field_types`, `get_field_statistics`, `get_missing_values`, `get_unique_values` |
| Temporal | `detect_temporal_columns`, `analyze_temporal_column`, `get_temporal_extent` |
| Spatial | `detect_spatial_columns`, `analyze_spatial_column`, `get_spatial_extent`, `get_spatial_extent_from_tuple_column` |

**Hybrid (1)** — `get_sample_items`: text chunks for a `TextContext`, DataFrame
head otherwise. The only tool written to serve both modalities.

## Finding 1 — the gating table does not know text or SQLite exist

`_get_tool_context_compatibility()` maps all 19 tools to
`{SINGLE_CSV, MULTI_CSV}`. Neither `ContextType.TEXT` nor `ContextType.SQLITE`
appears in it. Measured:

```
single_csv   18/19 tools   (get_relationships is multi_csv-only)
multi_csv    19/19 tools
sqlite        0/19 tools
text          0/19 tools
```

**Consequence: SQLite and text contexts cannot run at all.**
`validate_plan_tool_compatibility` rejects any step whose player owns tools but
has none compatible with the context. `metadata_generator` is auto-added to
every plan and owns two tools, so every plan against a text or SQLite context
fails validation and `Orchestrator.run` aborts before executing. Only `critic`
(zero tools) would survive validation.

The six modality-agnostic tools and the text branch of `get_sample_items` are
therefore currently unreachable on the modality they were written for. The
`_get_tabular_context()` gate added on 2026-07-08 is likewise never exercised
by a real text context — nothing reaches it.

This table is the blocker between `TextContext` and a running pipeline.

## Finding 2 — six tools can never be invoked by the pipeline

`Player.execute_task` fires *every* tool a role owns and chooses arguments by
substring-matching the tool name against
`["resource_info", "item_count", "field", "sample", "statistics", "missing", "unique"]`.
A match receives `(context_key, resource)`; everything else receives
`(context_key)` alone. No tool ever receives a `column`, `field`, `lat_column`,
or `lon_column`.

Probed all 19 through that dispatch. Thirteen succeed. Six raise a pydantic
`ValidationError` for a missing required argument, which the loop catches and
files into the prompt as an `"Error: ..."` string:

| Tool | Missing argument |
|---|---|
| `get_unique_values` | `field` |
| `analyze_temporal_column` | `column` |
| `analyze_spatial_column` | `column` |
| `get_temporal_extent` | `time_column` |
| `get_spatial_extent` | `lat_column`, `lon_column` |
| `get_spatial_extent_from_tuple_column` | `column` |

The pattern is exact: **every `detect_*` tool works; every tool that would act
on what was detected fails.** The dispatcher can express "which columns look
temporal" and cannot express "what is the date range of this column."

`spatial_temporal_specialist` owns 12 tools, and these 6 are precisely its
analysis and extent capabilities. It can detect that a column looks like a
date and can never compute the range. The temporal/spatial coverage fields it
exists to populate are exactly what it cannot produce.

**The fix was designed and never wired.** `configs.py:333` sets
`"tool_execution_mode": "llm"` with the comment *"Model-driven tool calls supply
required args (column, lat/lon, etc.)."* Nothing reads that key —
`Player.__init__` and `create_player_from_config` both ignore it, and the only
other mention is a comment at `config.py:114`. Honoring it would restore six
tools and roughly one full agent role.

## Interpretation

The auto-fire dispatcher is not merely less flexible than model-driven tool
selection; it is **structurally incapable of expressing the parameterized half
of the tool surface**. Any tool taking an argument beyond a resource name is
unreachable, and that category contains all the analysis tools. Noted in
`docs/philosophy.md` under "Risk of the assumptions".

Both findings share one root cause: the same knowledge — which tool works
where, and how to call it — is written down in three hand-maintained places
(`_get_tool_context_compatibility()`, `PLAYER_CONFIGS[role]["tools"]`, and the
keyword list in `Player.execute_task`), keyed by string, with nothing checking
them against each other. Each finding is drift between two of them. Adding a
modality multiplies the surface for the same class of bug, and no test catches
it, because the failure mode is *silent omission* — a tool that is simply never
offered.

## Unrelated crashes noticed while reading

- `PLANNING_TEMPERATURE` is used at `orchestrator/orchestrator.py:43` but never
  imported: `Orchestrator()` raises `NameError` without an explicit temperature.
- `main.py:138-140` passes `args.model_name` / `args.temperature` /
  `args.provider`, none of which the argument parser defines. The library entry
  point works; `python -m src.main` does not.

## Follow-ups

All resolved the same day in
[the change log](2026-07-09_capability-tool-registry.change.md):

1. ✅ Replace the compatibility table with capability-based gating (finding 1).
2. ✅ Derive tool dispatch from each tool's signature, and let the model call the
   parameterized tools (finding 2).
3. ✅ Both crashes above.
