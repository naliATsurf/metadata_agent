# Change log 2026-09-17 — The candidate judge splits into a column matcher and a passage reader

**Goal:** Stop asking columns and prose the same question. `CandidateJudge` showed a
model one field and a few BM25-ranked candidate cards and asked it to pick one. That fits
neither tier:

- **Columns are a matching problem.** The catalog is fixed and identical for every field,
  and the evidence is structured (units, value range, dtype). Ranking it per field hid
  most of the catalog from each field and repeated the rest across calls: fields group
  only when their candidate lists match *in order*, so the column tier took 11 calls for
  30 fields on `TRADAT031`.
- **Prose is a reading problem.** Whether a passage states a value, and where, is the
  whole question, and the quote is the evidence. Asked per field, the same passage was
  sent once per field that retrieved it — 11 more calls with all three readmes.

## Change

- **`src/router/judge.py`** keeps only what the two judges share: `candidate_ref`,
  `Verdict`, confidence grades, `weaker`, JSON parsing, and concurrent `dispatch`.
  `CandidateJudge`, `LLMCandidateJudge`, `describe` and `promote` are gone.
  `Verdict.grounded` is now `Optional` — `None` where a quote is not the evidence.
- **`src/router/column_matcher.py`** — `ColumnMatcher` / `LLMColumnMatcher`. Every field
  is matched against the whole catalog in one prompt, catalog first so calls over one
  catalog share a prefix. No quote is asked for: a column's card is its evidence.
  - **`merge_columns`** shows columns layer 3 resolved to the same meaning and units once.
    `TRADAT031`: 45 columns → 24 cards (`pH`, `tank`, `ID`, `nitrate`, `mass` repeat
    across tables). A match fans back out to every admissible member, so a field
    answered in six tables routes to all six. Undescribed columns never merge.
  - **`split_cards`** packs cards into slices under `MATCH_MAX_CHARS` (30 000); tools join
    every slice; fields are grouped at most `MAX_FIELDS_PER_CALL` (20) per call. A field
    that meets several slices keeps its strongest verdict.
- **`src/router/passage_reader.py`** — `PassageReader` / `LLMPassageReader`. Each retrieved
  passage is read once for every field that retrieved it; each answer must quote the
  sentence. The prompt now forbids shortened quotes and `...`, the cause of most
  unlocatable quotes found with `--debug`.
- **Router** (`route_fields(..., matcher=, reader=)`):
  - The **veto still runs before the matcher**: fields are partitioned by the set of
    columns vetoed for them, and each partition is one catalog. Veto sets are shared
    widely (every name field is ruled out of the same numeric columns), so there are few.
  - The document tier inverts per-field retrieval into per-passage reading, then picks
    each field's best passage: located quote, then confidence, then retrieval rank.
  - BM25 still runs with judges on. Without a judge rank 1 is the routing; with one the
    ranking is the record of what retrieval proposed, which recall@k measures.
- **CLI / UI:** `--llm-candidate-judge`, `--judge-workers` and `--no-judge-batch` keep their
  names and now drive both judges, which share one model. `build_candidate_judge` became
  `build_judges`. The demo's "Group identical candidate sets" is now "Ask about many
  fields per call".

## Measured (`sharetrait_basic_no_trait__TRADAT031`, all readmes, 8 workers)

| | per-field judge | matcher + reader |
|---|---|---|
| judge calls | 22 | **13** (5 matcher, 8 reader) |
| recall@5 | 12/12 | 11/12 |
| precision@1 | 8/12 | 8/12 |
| over-answered | 2/18 | **1/18** |
| accuracy | 24/30 | **25/30** |

`oxygen` is no longer over-answered with `p50` — the case the matcher was meant to fix by
showing conditions and responses side by side. `trait_type` regressed: the matcher did
not pick a measurement column and the reader then answered it from a passage. Its recall
drop is partly the record — a document-tier routing lists passages, not the columns BM25
ranked — but the miss is real. `genus_name` is still over-answered from the species
sentence, and `title_dataset` still cites `readme` where the label says `readme_hard`.

## Not done

Step 3 of the plan for very large catalogs — narrowing with each field's BM25 top-*n*
before matching — is not built. Merging plus splitting covers any catalog that fits a
modest number of slices; narrowing earns its place only when slices × veto sets becomes
more calls than per-field retrieval would have taken.
