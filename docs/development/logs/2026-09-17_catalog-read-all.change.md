# Change log 2026-09-17 — The catalog resolver reads every passage of a document with few enough

**Goal:** Give catalog resolution the field router's rule. The resolver chose a document's
read path by size: up to `catalog_whole_doc_max_chars` (20 000) it read the whole file,
past it BM25 picked the top 3 chunks per column. The router instead reads every passage
while there are at most `router_read_all_max_passages`, because BM25 misses a passage
that describes something without using its name. The two stages decided the same
question two ways.

## Change

- **`catalog_whole_doc_max_chars` is replaced by `catalog_read_all_max_passages`** (default
  1; see below). Each document is split into contiguous passages of up to
  `catalog_passage_max_chars` (20 000). A document with at most that many is read passage
  by passage for every unexplained column (`_read_all`, one call per passage); one with
  more is narrowed with BM25 as before (`_localized_reads`). Still decided per file. A
  README is one passage, so it still costs one call.
- **`pack_contiguous`** (`src/router/catalog.py`) splits chunks into contiguous passages
  for both the resolver and the router, which had its own copy.
- The settings panel shows the new threshold in place of the old one.
- `docs/development/notes/catalog-resolution.md` has a flowchart of the whole resolution,
  data and steps drawn apart, and its reader section describes the new rule.

## Measured

`TRADAT031`, `readme_long.txt` only (34 000 chars, 2 passages), no codebook so every
column is left to the reader. A: `catalog_read_all_max_passages=0` (BM25, the old path
for this file). B: the default (read both passages).

| | A: BM25 | B: read every passage |
|---|---|---|
| column names described | 24/24 | 24/24 |
| reader calls | 2 | 2 |
| claim comparer calls | 1 | 1 |

No gain in coverage: every column name appears in the text, so BM25 found them all. The
descriptions differ, and B is not better overall:

- **B better:** `fas` "Factorial aerobic scope" (A: "functional aerobic scope"); `aas` and
  `sgr` gained units.
- **B worse:** units given as "dimensionless" where A had them or left them empty (`no2`,
  `no3`, `survival`, `pH`, `tank`, `ID`, `p50`); `duration` "EPOC duration, h" where A had
  "recovery duration, minutes"; `no2`/`no3` lost "blood".

Likely cause: in B each 20 000-char passage is read for all 24 columns, including a
passage that never mentions a column, and the model fills the answer in anyway. In A a
column is read only with the chunks that mention it.

**So the default is 1, not the router's 10.** A document that fits one passage is read
whole; a longer one is narrowed with BM25 — the resolver's behaviour before this change,
under the shared rule. Raise it for a bundle whose documents describe columns without
naming them.
