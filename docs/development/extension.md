# Extension Plan: Free-Text Metadata Extraction

This document plans the extension of the framework to extract metadata from
free-text sources (plain text, and later Markdown/PDF), reusing the existing
orchestrator, player, and standards machinery. It complements
[Architecture](architecture.md) and the [Module Guide](modules.md).

## Motivation and approach

The `ExecutionContext` abstraction in `src/context/base_context.py` was
designed to represent any "world" the agents operate in — not just tabular
data. `ContextType.TEXT` already exists in the enum, and
`src/context/registry.py` already maps `.txt` to it, but no `TextContext`
implementation exists yet (`src/context/text_context.py` is a placeholder).

The plan is therefore to **extend, not fork**: implement the missing context
type and its tools, and leave the orchestrator/planner/player pipeline
untouched wherever possible. Everything downstream talks to the context
abstraction, so the work concentrates in `src/context/` and `src/tools/`.

## Phase 1 — `TextContext`

Implement `TextContext(ExecutionContext)` in `src/context/text_context.py`.

**Key design decision: the tabular representation of text.** The base
contract requires `read_resource()` to return a `pd.DataFrame`. The
convention:

- Each input file is one *resource* (mirroring `CSVContext`'s
  `{resource_name: path}` normalization of str/list/dict input).
- `read_resource(resource)` returns one row per **chunk** (paragraph by
  default, configurable to line/page/fixed-size), with columns such as
  `chunk_id`, `text`, `char_count`, `start_offset`.
- `iter_resource()` yields chunk batches, so large documents stream the same
  way large CSVs do.

This keeps generic tools like `get_sample_items`, `get_item_count`, and
chunked iteration working unchanged.

**`_load_resource_info()`** reports a `ResourceInfo` where:

- `item_count` = number of chunks, `size_in_bytes` = file size.
- `fields` describe the chunk columns above (`FieldInfo` dtypes are exact
  here; nullability/PK flags are simply left at defaults).
- `description` may carry a cheap extractive summary (first N characters),
  useful to seed downstream players without an LLM call.

`_discover_relationships()` stays as the base-class default (`[]`) initially;
cross-document relationships (shared entities, citations) are out of scope
until Phase 5.

## Phase 2 — Wiring: factory and classifier

- `ContextFactory._create_typed_context()` in `src/context/context_factory.py`
  currently dispatches only to `CSVContext` and `SQLiteContext`; add a
  `ContextType.TEXT` branch returning `TextContext`.
- `classify_context_type()` in `src/context/context_classifier.py` handles the
  single-file case via extension already, but the directory and multi-path
  branches only recognize CSVs. Extend both so a directory of `.txt` files
  (or a mixed list) classifies as `TEXT` (multi-document `TextContext`)
  rather than `UNKNOWN`.
- Extend `EXTENSION_MAP` in `src/context/registry.py` beyond `.txt` as new
  formats land (`.md` first; `.pdf` later behind an optional dependency).

## Phase 3 — Text tools

Most of `src/tools/context_tools.py` is column-oriented (field statistics,
missing values, FK discovery, temporal/spatial *column* detection) and does
not apply to prose. Add a `src/tools/text_tools.py` module with, at minimum:

- `get_sample_passages(context_key, resource, n)` — representative chunks
  (head, middle, tail) rather than the first rows.
- `search_text(context_key, query, resource="")` — keyword/regex search with
  surrounding context, the text analogue of `get_unique_values`.
- `get_document_stats(context_key, resource)` — chunk/word/character counts,
  average chunk length.
- `detect_language(context_key, resource)`.
- `extract_temporal_mentions` / `extract_spatial_mentions` — dates and place
  names found *in the content*, feeding the same
  `spatial_temporal_specialist` role that today reads coordinate columns.

Generic tools that already work through the abstraction
(`get_context_overview`, `list_resources`, `get_resource_info`,
`get_item_count`, `get_sample_items`) are reused as-is.

## Phase 4 — Players and planning

`PLAYER_CONFIGS` in `src/players/configs.py` assigns column-oriented tools to
each role. Two changes:

1. **Tool gating per context type.** Player configs should declare which
   tools apply to which `ContextType` (or, simpler first cut: the
   orchestrator filters each player's tool list against the active context
   type), so the planner never schedules `detect_spatial_columns` against
   prose.
2. **A `text_analyst` player** (or text-specific role prompts for
   `data_analyst`) whose prompt speaks in documents/passages rather than
   tables/columns, equipped with the Phase 3 tools. `metadata_generator`,
   `critic`, and `metadata_specialist` are largely context-agnostic and
   should carry over with little or no prompt change.

The planner prompt in `src/orchestrator/prompts.py` should mention the text
tools so generated `Plan` steps can reference them.

## Phase 5 — Standards and output schema

`src/standards.py` derives prompting templates and Pydantic output models
from `STANDARD_DEFINITIONS`. Add a document-oriented standard (working name
`document_general`) with fields along the lines of: `title`, `description`,
`subject`, `language`, `document_type`, `authors`, `temporal_coverage`,
`spatial_coverage` (textual, not bounding box), `keywords`. Existing
standards remain selectable — a text corpus about ecology can still target
`spatial_ecological` where its fields make sense.

Later in this phase: cross-document relationships (shared entities,
citations) via an overridden `_discover_relationships()`.

## Phase 6 — Tests, docs, demo

- Unit tests for `TextContext` (chunking, resource info, factory dispatch,
  classifier) mirroring the existing CSV/SQLite test structure under
  `tests/`.
- An end-to-end example in `examples/` running the pipeline over a small
  `.txt` corpus.
- Update the [Module Guide](modules.md) `src/context/` section and the demo
  app's accepted file types once the flow works.

## Known friction points

- **Tabular-shaped base contract.** `read_resource() -> pd.DataFrame` and
  `FieldInfo`'s dtype/nullability vocabulary are CSV/SQL idioms. The chunk
  convention absorbs this for now; if it chafes (e.g., for PDFs with layout),
  loosen the base contract rather than forking.
- **Chunking is a modeling choice.** Paragraph chunks suit metadata
  extraction (title, abstract, dates tend to be positional); make the
  strategy a `TextContext` constructor argument so experiments don't require
  code changes.
- **`.txt` is not always prose.** A `.txt` file may be delimited data; the
  classifier may eventually want a content sniff (like `CSVContext`'s
  delimiter sniffing) rather than trusting the extension alone.

## Suggested order of work

Phases 1–2 first (they unblock everything and are testable without an LLM),
then 3 and 5 in parallel, then 4, then 6. Phases 1–3 alone are enough for a
first end-to-end run using the existing generic players.
