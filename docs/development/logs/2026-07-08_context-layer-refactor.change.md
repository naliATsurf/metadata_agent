# Change log 2026-07-08 — Context layer refactor and new TextContext

**Goal:** Extend metadata extraction beyond tabular data to free text, with a
base abstraction clean enough to generalize to other modalities later. No
external API-compat constraint — prioritized clean design over minimal diff.
Existing pipeline preserved.

## Base abstraction split
- Split `ExecutionContext` into a modality-agnostic base (resource inventory,
  metadata, schema, relationships) and a new `TabularContext` subclass holding
  the DataFrame contract (`read_resource`, `iter_resource`,
  `get_field_values`). `CSVContext` / `SQLiteContext` now subclass
  `TabularContext`; behavior unchanged.
- Generalized `is_multi_csv` → `is_multi_resource` (`len(resources) > 1`) on the
  base and threaded through the pipeline (`plan_executor`, `step_executor`,
  `player`, `main`) and the serialized `to_dict()` / `get_schema()` key.
  **Fixes a latent bug:** multi-document contexts returned `is_multi_csv =
  False`, so the planner silently treated multi-file text corpora as
  single-resource.
- Deleted the unused `primary_resource` property (dead code).

## Typed resource metadata
- Split `ResourceInfo` into a universal base + `TabularResourceInfo` (fields,
  primary_key) + `TextResourceInfo` (char/word/line counts, language,
  encoding). Removed the interim `properties` bag in favor of typed subclasses.
- Added a `kind` discriminator, polymorphic `to_dict()`, and `summary()` per
  subclass, so the orchestrator and tools render per-modality without branching
  on context type.
- `RelationshipInfo`: `from_field` / `to_field` are now optional (supports
  resource-level links like `"cites"`, not just foreign keys); added
  `describe()` that degrades gracefully when fields are absent.

## TextContext (new)
- Implemented the previously-empty `TextContext`: `read_text`, `iter_chunks`
  (pluggable chunker; paragraph-based default), `get_chunks`, and `search`
  (keyword/regex with surrounding context). Added `TextChunk` and
  `paragraph_chunker` / `fixed_size_chunker`.

## Wiring
- Registry: added `.md` / `.markdown` / `.rst` → `TEXT`, plus `is_text_type()`.
- Classifier and factory now dispatch text for single files, lists, dicts, and
  directories; mixed CSV+text input classifies as `UNKNOWN` rather than
  silently coercing.

## Tool gating
- Added `_get_tabular_context()` gate in `context_tools.py`; column-oriented
  tools (field stats/types/names, missing values, unique values, all
  temporal/spatial column tools) now return a clear "requires a tabular
  context" message on text contexts instead of failing obscurely.
- `get_sample_items` is dual-mode (rows for tabular, chunks for text);
  `get_context_overview` serializes `info.to_dict()` polymorphically.

## Verification
- Pipeline imports OK, end-to-end smoke test (text + CSV paths), and the
  existing unit suite all pass (8 tests, 1 pre-existing skip).

## Not yet done
- `text_tools.py` (agent-facing `read_text` / `search` tools), a `text_analyst`
  player, and a `document_general` metadata standard.
- Plan in `docs/development/extension.md` predates the capability-split design;
  Phases 1 & 4 need updating.
