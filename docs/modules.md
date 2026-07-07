# Module Guide

This page walks through the purpose and internal structure of individual
modules, complementing the high-level [Architecture](architecture.md) diagram
and the auto-generated [API Reference](reference/index). 

## `src/context/`

### Purpose

This module answers one question for the rest of the system: *what data am I
looking at, and how do I read it?* — regardless of whether that's one CSV, ten
related CSVs, or a SQLite database. Everything downstream (orchestrator,
planner, tools, players) talks to this abstraction instead of touching
`pandas`/`sqlite3` directly.

### Files

**`base_context.py`** — the contract. `ExecutionContext` is an ABC with:

- Abstract members any new source type must implement: `context_type`,
  `resources`, `_load_resource_info()`, `read_resource()`, `iter_resource()`.
- Concrete helpers built on top of those: `get_resource_info()` (caches
  `_load_resource_info`), `get_schema()`, `get_relationships()` (caches
  `_discover_relationships()`, which defaults to `[]` unless overridden).
- Plain dataclasses `FieldInfo`, `ResourceInfo`, `RelationshipInfo` are the
  common vocabulary all context types report metadata in, so a `data_analyst`
  player can describe a CSV column and a SQL column identically.

**`csv_context.py`** — `CSVContext`. Normalizes any of str/list/dict input
into a `{resource_name: path}` map, sniffs the delimiter by counting
`,`/`\t`/`;`/`|` in the first 8KB, and — the most interesting part —
`_discover_relationships()` guesses foreign keys across CSVs by normalizing
column names (`user_id` ~ `userid`) and checking value-set overlap plus
uniqueness ratio to infer one-to-one/one-to-many/many-to-many with a
confidence score. This is pure heuristics (no LLM call), used to seed the
`relationship_analyst` player.

**`sqlite_context.py`** — `SQLiteContext`. Same interface, but relationships
come for free from `PRAGMA foreign_key_list` (confidence `1.0`,
`is_verified=True`) instead of being guessed. Filters out
`sqlite_sequence`/`sqlite_stat1` system tables.

**`registry.py`** — a small lookup table mapping file extension to
`ContextType` (`.csv`/`.tsv` → CSV, `.sqlite`/`.db` → SQLITE, `.txt` → TEXT
[not yet implemented], everything else → UNKNOWN). Also exposes
`is_csv_type()`, used throughout the codebase to mean "single_csv or
multi_csv."

**`context_classifier.py`** — a second, standalone classification path
(`classify_context_type`) that works off a raw list of paths rather than an
instance. It's used by the orchestrator specifically to decide single-vs-multi
CSV before a context object necessarily reflects that. Note there is some
overlap with `ContextFactory`'s own detection logic — both do extension
sniffing independently.

**`context_factory.py`** — the entry point, `create_context()`. Takes
str/list/dict/existing-`ExecutionContext` and:

1. If it's already an `ExecutionContext`, passes it through untouched.
2. If it's a directory, globs `*.csv` and builds a `CSVContext`.
3. Otherwise detects type from extension and dispatches to `CSVContext` or
   `SQLiteContext`.
4. Anything not CSV/SQLite currently falls through to `CSVContext` as a
   catch-all default (see `_create_typed_context`'s `else` branch) — this is a
   soft spot: an unrecognized type silently gets treated as CSV rather than
   raising.

### How it's used downstream

The orchestrator calls `create_context(source)` once and gets back one object
with a `context_type`. Everything branches off that: which planning prompt to
use (single vs. multi), which tools are compatible
(`filter_tools_by_context_type` in `src/tools/context_tools.py` checks against
`ContextType`), and whether `relationship_analyst` gets added to the player
pool.

This is the cleanest extension point in the codebase — adding a new source
type means writing one class here plus a registry entry, not touching the
orchestrator's control flow. See "Adding New Data Source Types" in
[Architecture](architecture.md) for the steps.