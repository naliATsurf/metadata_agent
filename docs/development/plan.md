# Extension Plan: Free-Text (and Multi-Modality) Metadata Extraction

This document plans the extension of the framework to extract metadata from
free-text sources (plain text, Markdown; images/PDF later), reusing the
existing orchestrator, player, and standards machinery. It complements
[Architecture](../architecture.md) and the [Module Guide](../modules.md).

Status legend: ✅ done · 🟡 partial · 🔲 not started.

## Motivation and approach

The `ExecutionContext` abstraction was always meant to represent any "world"
the agents operate in, not just tabular data. But the original base contract
leaked tabular assumptions: `read_resource() -> pd.DataFrame`, `FieldInfo`
dtypes, `is_multi_csv`. Rather than force every new modality to fake a table,
the adopted approach is a **capability split**: keep a small modality-agnostic
base and push data-access shape into subclasses.

We **extend, not fork**: the orchestrator/planner/player pipeline is untouched
except where it rendered tabular vocabulary, and the work concentrates in
`src/context/` and `src/tools/`.

## Context module refactor ✅

Refactor the context module to accommodate multiple modalities. All of the
following is done and verified (smoke test + `python -m unittest discover
tests`; existing pipeline green).

- **Base/`TabularContext` split.** `ExecutionContext` now holds only the
  modality-agnostic contract (`resources`, `get_resource_info`, `get_schema`,
  `get_relationships`, `validate`). The DataFrame contract (`read_resource`,
  `iter_resource`, `get_field_values`) moved to a new
  `TabularContext(ExecutionContext)`. `CSVContext` / `SQLiteContext` subclass
  it; behavior unchanged.
- **Typed resource metadata.** `ResourceInfo` split into a universal base +
  `TabularResourceInfo` (fields, primary_key) + `TextResourceInfo`
  (char/word/line counts, language, encoding). Dropped the interim
  `properties` bag. Each carries a `kind` discriminator and overrides
  `to_dict()` and `summary()`, so consumers render per-modality without
  branching.
- **Resource-level relationships.** `RelationshipInfo.from_field` /
  `to_field` are now optional and it grew a `describe()`; relationships can be
  whole-resource (e.g. `"cites"`, `"shared-entity"`), not only foreign keys.
- **De-CSV'd the universal surface.** `is_multi_csv` → `is_multi_resource`
  (`len(resources) > 1`), threaded through `plan_executor`, `step_executor`,
  `player`, `main`, and the serialized `to_dict()`/`get_schema()` key. This
  also fixed a latent bug: multi-document contexts had returned
  `is_multi_csv = False`, so the planner silently treated multi-file text
  corpora as single-resource. Deleted the unused `primary_resource` property.

## Phase 1 — `TextContext` ✅

Implemented in `src/context/text_context.py` (previously a placeholder).
`TextContext` subclasses `ExecutionContext` **directly** (not
`TabularContext`) — there is no DataFrame contract to satisfy. Data access is
document-oriented:

- `read_text(resource, limit=None)` — full text, cached.
- `iter_chunks(resource, chunker=None)` / `get_chunks(resource)` — yields
  `TextChunk` (resource, index, text, start_offset, char_count). Chunking is a
  read-time concern via a pluggable `chunker` callable; default is
  `paragraph_chunker` (blank-line split), with `fixed_size_chunker` provided.
- `search(query, resource=None, regex=False, ...)` — keyword/regex matches
  with surrounding context across one or all resources.
- `_load_resource_info()` returns a `TextResourceInfo` (item_count = chunk
  count, plus char/word/line counts, encoding, and a short extractive preview
  in `description`).

Each input file is one resource, mirroring `CSVContext`'s str/list/dict
normalization. `_discover_relationships()` uses the base default (`[]`) for
now — see Phase 5.

## Phase 2 — Wiring: registry, classifier, factory ✅

- `registry.py`: `EXTENSION_MAP` maps `.txt` / `.md` / `.markdown` / `.rst` →
  `ContextType.TEXT`; added `is_text_type()`.
- `context_classifier.py`: directory and multi-path branches now recognize
  text, so a folder or list of text files classifies as `TEXT`. Mixed
  CSV+text input classifies as `UNKNOWN` rather than silently coercing.
- `context_factory.py`: dispatches `TextContext` for a single path, list,
  dict, or directory of text files.

## Phase 3 — Text tools 🔲

The column-oriented tools in `src/tools/context_tools.py` (field statistics,
missing values, FK discovery, temporal/spatial *column* detection) are now
**gated** to tabular contexts (see Phase 4), but agents still have no
text-specific tools. Add a `src/tools/text_tools.py`:

- `get_sample_passages(context_key, resource, n)` — representative chunks
  (head/middle/tail).
- `search_text(context_key, query, resource="")` — wraps `TextContext.search`.
- `get_document_stats(context_key, resource)` — chunk/word/character counts.
- `detect_language(context_key, resource)`.
- `extract_temporal_mentions` / `extract_spatial_mentions` — dates and place
  names found *in the content*, feeding the existing
  `spatial_temporal_specialist` role.

## Phase 4 — Players and planning 🟡

- **Tool gating 🟡.** Done *structurally*, not yet per-config: column tools
  call `_get_tabular_context()` and return a clear "requires a tabular
  context" message on text contexts; `get_sample_items` is dual-mode (rows vs.
  chunks); `get_context_overview` serializes `info.to_dict()` polymorphically.
  Still open: declaring per-player tool applicability by `ContextType` so the
  planner never *schedules* a tabular tool against prose in the first place.
- **`text_analyst` player 🔲.** Add a role (or text-specific prompts for
  `data_analyst`) that reasons in documents/passages, equipped with the Phase 3
  tools. `metadata_generator` / `critic` / `metadata_specialist` are
  context-agnostic and carry over. Mention the text tools in the planner
  prompt (`src/orchestrator/prompts.py`).

## Phase 5 — Standards and output schema 🔲

Add a document-oriented standard to `STANDARD_DEFINITIONS` in
`src/standards.py` (working name `document_general`): `title`, `description`,
`subject`, `language`, `document_type`, `authors`, `temporal_coverage`,
`spatial_coverage` (textual), `keywords`. Existing standards stay selectable.

Cross-document relationships (shared entities, citations) via an overridden
`TextContext._discover_relationships()` — the `RelationshipInfo` groundwork is
already in place (see Context module refactor).

## Phase 6 — Tests, docs, demo 🔲

- Promote the end-to-end smoke test into committed unit tests under `tests/`
  (chunking, `TextResourceInfo`, factory dispatch, classifier, tool gating),
  mirroring the CSV/SQLite structure.
- An `examples/` script running the pipeline over a small text corpus.
- Update the [Module Guide](../modules.md) `src/context/` section and the demo
  app's accepted file types.

## Friction points and notes

- **Tabular-shaped base contract — resolved.** The old contract forced text
  into a fake table; the capability split (base + `TabularContext` +
  `TextContext`) removes that. New modalities add a subclass, not a shim.
- **Chunking is a modeling choice — addressed.** The strategy is a `chunker`
  argument, so experiments need no code change.
- **Capability discovery is implicit.** Consumers ask
  `isinstance(ctx, TabularContext)`. Fine for two modalities; if a third
  overlaps (PDF = text + images), introduce explicit capability protocols /
  a `capabilities` set rather than a deep inheritance tree.
- **`.txt` is not always prose.** A `.txt` file may be delimited data; the
  classifier may eventually want a content sniff (like `CSVContext`'s
  delimiter sniffing) rather than trusting the extension alone.
- **`ContextType` is a closed enum.** Adding `IMAGE` / `PDF` is a one-line
  edit there plus a registry mapping; acceptable, not a base-class concern.

## Remaining order of work

The refactor and Phases 1–2 are done. Phase 3 (text tools) unblocks a real
agent run on text; then Phase 4's `text_analyst` + planner wiring; then Phase
5 (standard) for useful output; Phase 6 (tests/example/docs) throughout.
Phases 3–5 are the minimum for a first end-to-end text extraction.
