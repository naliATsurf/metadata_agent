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

## Tool module refactor ✅

Done 2026-07-09 (see the [change log](09-07-2026_change.log.md)); it was not in
the original plan but was forced by the same tabular assumptions.

- **Capability gating.** Tools declare `@context_tool(toolset=...,
  requires=TabularContext)`; `tools_for(context)` is an `isinstance` check.
  Deleted the hand-maintained context-type compatibility table. A new modality
  is unlocked by a context subclass, not a table edit.
- **Dispatch derived from the signature.** `auto_fireable` / `resource_scoped`
  are read off each tool's own argument schema. `Player.execute_task` now runs
  a **survey** phase (fire every auto-fireable tool) then an **investigate**
  phase (bind the rest to the model, seeded with the survey). This made the six
  parameterized tools reachable for the first time, and exposed two bugs: the
  `detect_*_columns` tools had only ever scanned `resources[0]`, and
  `get_relationships` was gated on format when it needed cardinality.
- **Roles request toolsets, not tools.** `PLAYER_CONFIGS` carries `"toolsets":
  ["universal", "tabular.spatial"]`, resolved against the live context at
  player construction. Roles are modality-independent.
- **Layout.** `src/tools/{base,universal}.py` + `tabular/{detection,profiling,
  temporal,spatial}.py`, keyed on capability (`tabular/` serves CSV *and*
  SQLite). Deleted `context_tools.py` and the unused `pandas_tools.py`.

Text and SQLite contexts had previously failed plan validation and aborted
every run; both now execute (SQLite 18/19 tools, text 6/19).

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

Text contexts run, but on the six `universal` tools only — agents still have no
text-specific tools. Add a `src/tools/text/` package whose tools declare
`requires=TextContext`; the tool module refactor above is what makes this a
matter of adding a file rather than editing a gating table.

- `get_sample_passages(context_key, resource, n)` — representative chunks
  (head/middle/tail).
- `search_text(context_key, query, resource="")` — wraps `TextContext.search`.
- `get_document_stats(context_key, resource)` — chunk/word/character counts.
- `detect_language(context_key, resource)`.
- `extract_temporal_mentions` / `extract_spatial_mentions` — dates and place
  names found *in the content*, feeding the existing
  `spatial_temporal_specialist` role.

## Phase 4 — Players and planning 🟡

- **Tool gating ✅.** Resolved by the tool module refactor. `tools_for_role()`
  expands a role's toolset globs against the live context, so a player is never
  *constructed* holding a tool the context cannot serve, and plan validation
  passes on every context type. Sampling went polymorphic too: the
  `isinstance(ctx, TextContext)` branch in `get_sample_items` became an
  abstract `ExecutionContext.preview()`.
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

The context refactor, the tool refactor, Phases 1–2, and Phase 4's tool gating
are done. Phase 3 (text tools) unblocks a *useful* agent run on text — text
contexts already execute, but see only universal tools. Then Phase 4's
`text_analyst` + planner wiring; then Phase 5 (standard) for useful output;
Phase 6 (tests/example/docs) throughout. Phases 3–5 remain the minimum for a
first end-to-end text extraction.

Two loose ends from the tool refactor: the investigate phase has only been
exercised against a scripted LLM, never a live provider; and roles now see a
slightly wider tool set than their old hand-listed one (intentional, but the
output-quality impact is unmeasured).
