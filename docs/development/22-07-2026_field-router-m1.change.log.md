# Change log 2026-07-22 — Field router M1: schema walker, Searchable capability, BM25 search

**Goal:** Land milestone 1 of the field-driven router
([plan_field_router.md](plan_field_router.md)) — the two foundation layers that
are testable with no planner change yet. Layer 1 flattens a metadata standard to
the leaf fields the router must fill. Layer 2 gives every modality a `search`
capability that returns *pointers* (a column to compute on, a text span to quote)
rather than values, exposed as a provenance-captured tool. Neither the
orchestrator nor the players change; this is scaffolding the later milestones
build on. A follow-up pass then replaced the placeholder ranker and chunker with
stronger, still-deterministic ones.

## Layer 1 — the schema walker

`src/router/` (new package). `walk_schema(model) -> List[FieldSpec]` flattens a
Pydantic standard to leaf fields, because a schema is a tree — nested models,
optionals, unions — not the flat list a naive `model_fields` walk assumes. It
unwraps `Optional`/`Union` (marking those fields not-required), recurses into
nested models with a dotted path, and treats containers (`list`/`dict`) as
leaves. `FieldSpec.description` doubles as the router's query, so a schema with
thin descriptions is a routing risk the walker surfaces rather than hides.

## Layer 2 — the `Searchable` capability

**A capability, gated like `TabularContext`.** `Searchable(ExecutionContext)` and
`EvidenceRef` are new in `src/context/base_context.py`. `TabularContext` and
`TextContext` are reparented to `Searchable` and each implement `search`; a
context is searchable iff it can rank its own contents. Tools gate on the one
capability (`requires=Searchable`), so the router routes over any modality
without branching — the same isinstance-gating the rest of the tool layer uses.

**Search returns pointers, not answers.** `EvidenceRef{resource, locator, kind,
snippet, score}` is a candidate location: for tabular, `locator` is a column name
and `kind="computed_column"` (a computation then runs on it — recomputable); for
text, `locator` is a `(start, end)` char span and `kind="quoted_span"` (an
extractor then quotes it). `kind` is the assurance carrier the later provenance
grade reads.

**`TabularContext.search` is the generalization of `detect_*_columns`.** From two
hardcoded field types (spatial, temporal) to any query: score each column's name,
description, and samples against the query and return the top matches. Opaque
names score ~0 by construction — the semantic gap catalog resolution (layer 3)
will close — so a low score here is a signal, not a bug, and there is a test
asserting exactly that.

**One tool, `search_context` (`src/tools/search.py`).** A one-line delegation to
`ctx.search`, gated `requires=Searchable`. The method is the mechanism (typed,
per-modality, in-process); the tool is the instrumented boundary — it takes a
`context_key` string, returns JSON, and funnels through the standard tool site so
every retrieval is capability-gated, cached, and **captured in the evidence
ledger** (the reason the router must route via the tool: hits become provenance
citations with `phase="route"`). It is correctly *not* auto-fireable — it needs a
query — so the deterministic survey never fires it.

## Ranking and chunking — the follow-up refinements

The first cut used a naive scorer and a blank-line chunker; both were replaced.

**(a) Span/offset exactness.** `iter_chunks` stripped a chunk's text but kept the
unstripped `start_offset`, so `text[locator] != chunk.text` when a chunk had
leading whitespace — approximate spans, unfit for provenance-grade citation. The
offset is now advanced past the stripped leading whitespace, restoring the
invariant `text[start : start + len(text)] == chunk.text`. A test asserts it for
every chunk.

**(b) BM25 ranking + a stop-word gate.** The set-overlap scorer ignored term
rarity, so `search("licence for reuse")` surfaced a chunk matching only the
stop word "for". It is replaced by Okapi BM25 (`bm25_scores`) over the candidate
documents — rewards rare, discriminative terms; normalizes for length; stays
deterministic and dependency-free, which keeps results replayable (the assurance
grade depends on it). A small query-side stop list (`content_terms`) is the noise
gate: a query of only stop words now abstains (returns nothing) rather than
matching on function words. Both tabular and text search share the scorer.

**(c) Heading-aware Markdown chunking.** `markdown_chunker` splits *before* each
Markdown heading, so each chunk is a section — heading line plus its body. The
heading travels with the content it introduces, and because a chunk literally
*begins* with its heading, the heading's terms are part of what `search` ranks (a
section under `## Access and reuse` scores for a `license` query) with no separate
context field and no break to the span/offset contract. The chunker is chosen
from content — heading-aware for Markdown, paragraphs otherwise — so `.txt` is
unaffected; an explicit chunker still overrides everywhere.

**Rename: `TextContext.search` grep → `grep`.** The pre-existing `search` was a
literal/regex match returning all hits — a different operation from the fuzzy,
ranked capability. It is renamed `grep` (what it is), freeing `search` for the
capability. Its tests moved to `TestGrep`.

## Test fixture and standard

`data/tests/router_test/` (a bundle: opaque-column CSV, a codebook, a README)
and the `field_router_test` standard in `src/standards.py` were added earlier this
session as the bed for exercising all of the above — the semantic gap, cross-file
resolution, narrative fields, and two planted conflicts. Their expected fills and
traps live in the [plan doc's Test fixture section](plan_field_router.md#test-fixture),
deliberately kept out of the bundle so the router cannot ingest its own answer
key. (The bundle lives under `data/tests/`, tracked with the repo so the demos
here are reproducible from a clean checkout.)

## Verification

- `pytest` — **121 pass, 1 skip** (was 99 before M1), the 1 warning pre-existing.
- New tests: `tests/test_router_schema.py` (schema walker: nesting, optionality,
  container-as-leaf, a real standard) and `tests/test_searchable.py` (tabular and
  text search, the opaque-name gap, the stop-word gate, ranking, and the
  `search_context` tool: offered, not auto-fireable, serializable, captured as
  evidence). `tests/test_text_context.py` gained span-exactness and Markdown-
  chunking tests and the `grep` rename.
- Demonstrated on the README fixture: 6 heading-aware chunks; `span == chunk.text`
  exactly; `search("for the of")` abstains; `"licence for reuse"` routes to the
  `## Access and reuse` section.

## Scope and limits

- **The lexical ceiling is real and shows.** On the fixture, `"how the data was
  collected"` routes to `## Files`, not `## Methods` — Methods never says "data"
  or "collected" literally. BM25 cannot bridge vocabulary mismatch; this is
  exactly the narrative-field case dense/embedding retrieval is for, deferred as
  an open decision in the plan. Lexical was chosen now because it is deterministic
  and replayable; the embedding path is graded lower-assurance by construction.
- **Markdown chunks can be coarse.** A long section with sub-bullets stays one
  chunk. Recursive/target-size chunking is a later refinement; heading-level is
  the improvement that matters for description docs.
- **No planner change yet.** M1 is scaffolding. The router that consumes
  `FieldSpec`s and drives `search_context` (with `phase="route"`) is M3; the
  catalog-resolution pass that closes the opaque-name gap is layer 3.
