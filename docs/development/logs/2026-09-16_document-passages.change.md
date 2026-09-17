# Change log 2026-09-16 — The document tier offers whole passages, not its best three chunks

**Goal:** Stop discarding the paragraph that holds the answer. The document tier kept
each field's top `k` chunks by BM25, and on `TRADAT031` that dropped chunk 0 of
`readme_long` — the one paragraph naming the species, the acclimation temperature, the
photoperiod, the food and the life stage — for *every* one of those five fields. It was
never ranked because a field description is written in schema vocabulary and the document
in the author's: `photoperiod schedule of daylight and night hours` against "a 12:12-h
light–dark cycle" scores **zero**, and so does every other lexical scorer. Recall@5 over
the answerable fields was 5/12.

This is not a ranking to be improved. The term is absent, and a filter cannot return what
it discarded. Meanwhile the entire document set is ~10 000 tokens — the filter was
enforcing a budget that was never under pressure.

## Change

- **`_document_corpus`** (`src/router/route.py`) packs each document's chunks, in order,
  into contiguous passages of up to `_PASSAGE_MAX_CHARS` (20 000 — layer 3's packing
  width), built once per routing pass rather than per field. **`_search_docs` then
  *orders* those passages instead of filtering chunks.** With a small document set every
  passage survives the top-`k` cut; with a large one `k` passages still cover an order of
  magnitude more text than `k` chunks did.
- Packing is **contiguous**, unlike layer 3's packing of scattered retrieval hits, because
  a router candidate's locator is a span: a contiguous passage keeps `text[start:end]`
  exactly the passage, so a citation located inside it stays a true document offset.

### Two concessions, both gated on the judge

The rule that fell out, and the one worth remembering: **the router only widens what it
offers when something downstream can narrow it again.**

- **`packed=judge is not None`.** With a judge, whole passages go out and come back
  narrowed to the cited sentence (`_cite`). Without one, rank 1 *is* the answer, so a
  passage-wide candidate would hand the compiler a section where it used to get the right
  paragraph, and every field of one small document would seed the identical span
  (caught by `test_router_localizes_distinct_spans_within_the_one_document`).
- **`offer_unscored=judge is not None`.** When *nothing* scores — the `photoperiod` case —
  the passages are offered anyway, since that is the only way such a field is ever
  answered. Only with a judge: it can answer "none", where a bare ranking takes rank 1 on
  faith. Without one an empty result stands and the field is reported `unanswered`, which
  is the honest reading of a corpus the query cannot reach.

### Grouping

`choose_many` groups only fields whose candidate refs match *in order*. Measured on
`TRADAT031` with batching on: routing `readme_long.txt` alone, the document tier takes
**2 calls** and the column tier 11, for 30 fields. With all three readmes the document
tier takes 11 calls, because BM25 orders the four passages differently per field.
Grouped fields stop being independent — a model shown one passage and nine fields tends
to distribute answers among them — so `--no-judge-batch` is still the comparison to run.

## Measured (`sharetrait_basic_no_trait__TRADAT031`, judge on)

| | chunks | passages |
|---|---|---|
| recall@5 | 5/12 (42%) | **12/12 (100%)** |
| precision@1 | 4/12 (33%) | 8/12 (67%) |
| over-answered | 3/18 | 2/18 |
| accuracy | 19/30 (63%) | **24/30 (80%)** |
| abstained with an answer present | 6 | 2 |
| cited correctly | 2/3 | 6/7 |

Over-answering was the risk — more material in front of the judge inviting it to answer —
and it did not materialise: 2/18, no worse than filtering. Three failures remain, none of
them retrieval: `genus_name` and `oxygen` are over-answers the judge should have refused
(no taxonomy is stated anywhere; `p50` is a fish response, not the tank's oxygen), and
`title_dataset` cites the short `readme` where the label names `readme_hard` — three
variants of one README, which `--search-doc` now exists to separate.

## Considered and not done

**Enriching each chunk with an LLM pass** — layer 3's move (inject the missing vocabulary
so a lexical query can reach it) applied to documents. Rejected: the LLM is already the
candidate judge here, and a second pass over the same prose in a near-identical role, only
with a different output shape, buys reach the judge already has once the passage is in
front of it.

**Embedding retrieval** — deferred, deliberately, and worth restating why rather than
leaving it as a `TODO`:

- It would close the vocabulary gap that defeats BM25 (`photoperiod` ↔ "12:12-h
  light–dark cycle"), which is the one thing packing does *not* fix — packing works here
  because the corpus is small enough to show whole, not because retrieval got smarter.
- It costs the bit-exact replay the field-router plan lists as a design risk: embeddings
  and ANN are not bit-stable, and the persisted query-plus-candidates is meant to re-rank
  identically for the verifier.
- At 29 chunks an index is pointless — it is a brute-force cosine over 29 vectors.

So it earns its place only when a bundle is too large to offer whole, which is the same
threshold that would make `k` passages a filter again. Until a bundle crosses it,
embeddings would add a dependency and forfeit replay to solve a problem the corpus size
already solves. Revisit there, not before.
