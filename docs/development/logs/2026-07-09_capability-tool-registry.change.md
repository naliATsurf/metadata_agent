# Change log 2026-07-09 — Capability-based tool registry

**Goal:** Fix the two silent failures found in the tool surface audit (see
[the analysis log](2026-07-09_tool-surface-audit.analysis.md)) and restructure
`src/tools/` so the same class of bug cannot recur as new modalities are added.
Both findings traced to one cause — the same knowledge written down in three
hand-maintained places — so the fix was to make each fact derivable from a
single declaration rather than to patch each table. No API-compat constraint;
prioritized clean design over minimal diff. Existing CSV pipeline preserved.

## Capability gating replaces the context-type table

- Tools now declare the *capability* they need from a context, not the formats
  they were known to work on:

  ```python
  @context_tool(toolset="tabular.profiling", requires=TabularContext)
  def get_field_names(ctx: TabularContext, resource: str = "") -> List[str]:
      ...
  ```

- `tools_for(context)` is an `isinstance` check against `requires`. Deleted
  `_get_tool_context_compatibility()`, `filter_tools_by_context_type()`,
  `get_tools_for_context_type()`, `get_single_csv_tools()`,
  `get_multi_csv_tools()`. **Adding a modality now means adding a context
  subclass** — there is no table left to forget.
- The decorator resolves the `context_key`, enforces `requires`, and passes a
  live `ctx` into the tool body. Removed the `_get_tabular_context()` guard and
  the `try/except` wrapper from all 19 tool bodies. **Tools now raise** instead
  of returning `{"error": ...}` / `"Error: ..."` that a model would read as data.
- `available_when=` is the deliberate escape hatch for constraints capability
  cannot express. Used exactly once: `get_relationships` needs *cardinality*
  (`lambda ctx: ctx.is_multi_resource`), not a capability.

## Dispatch derived from each tool's signature

- `auto_fireable` — true when `context_key` and `resource` are a tool's only
  required arguments. `resource_scoped` — true when it takes a `resource`.
  Both read off the tool's own argument schema; **deleted the name-keyword list**
  in `Player.execute_task` that guessed arguments by substring-matching tool
  names.
- `Player.execute_task` now runs two phases:
  1. **survey** — fire every auto-fireable tool (once per target resource, or
     once per context). Deterministic and cheap.
  2. **investigate** — bind the remaining tools to the model and run a
     tool-calling loop *seeded with the survey*, so the model chooses column
     names it has actually seen. Capped by `PLAYER_MAX_TOOL_ITERATIONS`; the
     runner always overrides `context_key` so a hallucinated key cannot be used;
     degrades to survey-only on providers without tool calling.
- **Fixes the audit's finding 2:** the six parameterized tools
  (`get_unique_values`, `analyze_temporal_column`, `analyze_spatial_column`,
  `get_temporal_extent`, `get_spatial_extent`,
  `get_spatial_extent_from_tuple_column`) are reachable for the first time.
- Honored the dead `tool_execution_mode` config key at last:
  `PLAYER_TOOL_EXECUTION_MODE` now selects `"investigate"` (default) or
  `"survey"`. Removed the per-role `"tool_execution_mode"` that nothing read.

## Roles request toolsets, not tools

- `PLAYER_CONFIGS` entries carry `"toolsets": ["universal", "tabular.spatial"]`
  — fnmatch globs over dotted toolset names — resolved against the live context
  at player construction (`tools_for_role`).
- Roles are now modality-independent. The same `data_analyst` gets column
  statistics on a CSV and only universal tools on a text corpus, without either
  the role or the tools knowing about the other.
- **Behavior change:** roles see a slightly wider tool set than their old
  hand-listed one (e.g. `data_analyst` gains `get_field_types`,
  `get_unique_values`, `get_context_schema`). Intentional, but it changes what
  those agents observe; output-quality impact unmeasured.

## Sampling is polymorphic, not branching

- `get_sample_items` used to test `isinstance(ctx, TextContext)`. Added abstract
  `ExecutionContext.preview(resource, n)`, implemented on `TabularContext`
  (DataFrame head) and `TextContext` (leading chunks). The last modality branch
  leaves the tool layer; the pattern matches the existing polymorphic
  `ResourceInfo.to_dict()` / `summary()`.

## New layout

```
src/tools/
  base.py           registry, @context_tool, tools_for, resolve_toolsets
  universal.py      6 tools — need only ExecutionContext
  tabular/
    detection.py    pure column heuristics; no context, no registry
    profiling.py    5 tools
    temporal.py     3 tools
    spatial.py      4 tools
```

Packages are keyed on **capability, not format**: `tabular/` serves CSV *and*
SQLite. Deleted `context_tools.py` and the unused `pandas_tools.py`.

## Two bugs the restructure exposed

- `detect_temporal_columns` / `detect_spatial_columns` declare
  `resource: str = ""`, so the old name-keyword dispatcher routed them as
  *context-level* tools and invoked them with no resource. **On a multi-resource
  context they only ever scanned `resources[0]`.** Signature-derived
  `resource_scoped` now runs them once per target resource.
- `get_relationships` was gated to `MULTI_CSV` by the old table, conflating
  cardinality with capability. Now expressed as `available_when`.

## Also fixed

Two crashes on the main entry path, found while reading:

- `PLANNING_TEMPERATURE` was used at `orchestrator.py:43` but never imported —
  `Orchestrator()` raised `NameError` unless given an explicit temperature.
- `main.py` read `args.model_name` / `args.temperature` / `args.provider`, none
  of which the parser defined — `python -m src.main` raised `AttributeError`
  before reaching the orchestrator. Added the three arguments.

## Measured

```
                before        after
single_csv      18/19         18/19    (unchanged)
multi_csv       19/19         19/19    (unchanged)
sqlite           0/19         18/19
text             0/19          6/19
unreachable tools    6            0
```

Text and SQLite contexts previously failed plan validation and aborted every
run, because `metadata_generator` is auto-added to every plan and owned two
tools that the table marked CSV-only.

## Verification

- `python -m unittest discover tests`: **60 pass, 1 skip** (was 32 pass, 1 skip).
  New: `test_tool_registry.py` (capability gating, flag derivation, toolset
  globbing, plan validation) and `test_player_tool_dispatch.py` (survey,
  investigation loop against a scripted LLM, context-key override, graceful
  degradation without tool-calling support).
- Smoke test: all 7 roles surveyed against all 4 context types on real data —
  28 combinations, zero tool errors, plan validation passing for each.
- `ruff check src/ tests/` clean; Sphinx builds clean.

## Docs updated

`philosophy.md` (two-phase design; the general lesson that a side table
restating what a type already knows will drift from it), `architecture.md`
(toolset table, adding-a-tool and adding-a-modality recipes), `modules.md`,
`plan.md` Phase 3, and `tutorial.md` — the last had import statements that
would have failed after the rename.

## Not yet done

- The `text/` toolset (`read_text`, `search`) is still unwritten. Text contexts
  run now, but on the 6 `universal` tools only — Phase 3 of the extension plan,
  and the place the new structure is meant to pay off.
- The investigation phase is exercised only by a scripted LLM, never a live
  provider; first real run may need prompt tuning on tool-call formatting.
- The debate loop is untouched: players within a step remain clones of one role
  and the synthesizer is one of the debaters.
