# Change log 2026-09-15 — A long document is read in packed passages, not chunk by chunk

**Goal:** Stop a long document from multiplying LLM calls. Past `_WHOLE_DOC_MAX_CHARS`
(20 000 characters) the prose reader retrieves each residual column's top 3 chunks, and
it used to make one call per distinct retrieved chunk. On TRADAT031, `readme_long.txt`
(34 401 characters, no codebook) retrieved 19 paragraphs for 23 columns: 19 calls, many
over 1–3 columns and a few hundred characters, sending about as much text in total as the
whole file would in one call. The 2 290-character `readme.txt` costs 1 call.

## Change

`_batch_prose_reads` (`src/router/catalog.py`) keeps retrieval as it was and changes
reading:

- The distinct retrieved chunks are packed, in document order, into passages of up to
  `_PASSAGE_MAX_CHARS` (20 000, the whole-file limit), joined by a blank line
  (`_pack_passages`). A chunk longer than the budget is a passage of its own; chunks are
  never split.
- Each passage is one `read_many` call over every column that retrieved a chunk in it.
- A read is grounded in the chunk its quote is found in, the column's own retrieved
  chunks first, so citations remain document offsets. A quote found in no single chunk
  is cited to the column's best-ranked chunk in the passage and marked unconfirmed.
- A column gets at most one read per passage; its reads are ordered by the best
  retrieval rank each passage held for it.

On `readme_long.txt` the count drops from 19 calls to 2 (19 498 and 1 449 characters).

## Consequences

- A column may now be asked about chunks retrieved by other columns in the same passage,
  so a paragraph that defines it without being among its top 3 can still resolve it.
- A quote that spans two packed chunks cannot be located and is graded `low`.
- `CachedProseReader` keys on the passage text, so a cache warmed before this change does
  not carry over (it is in memory only).

Tests: `test_long_doc_reads_retrieved_chunks_in_one_passage` and
`test_passages_split_at_the_budget` in `tests/test_catalog.py`.

## Also: unconfirmed evidence keeps a read at `low`

A read whose quote cannot be found in its source is graded `low` with an "evidence
unconfirmed" conflict. `_decide` then overrode that grade when a column had several reads:
disagreeing reads raised it to `medium`, and a read with the same wording raised it to
`high` as corroboration, while the conflict still said the evidence was unconfirmed.
Packing makes several reads less common, but a column read from a short file and a long
one still gets two.

`_decide` now:

- chooses a read whose quote was found over one whose quote was not, after the value
  check and before source order;
- keeps a chosen unconfirmed read at `low`, whatever the other reads say;
- does not count an unconfirmed read as corroboration.

Tests: the four `*unconfirmed*` / `*found_quote*` tests in `ProseReaderTierTest`.
