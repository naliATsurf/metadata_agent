# Change log 2026-09-17 — A document routing lists the sentences that answer the field, and nothing else

**Goal:** With the passage reader reading every passage for every field, a routing
listed every passage read: the cited sentence first, then each other passage whole,
whether or not it said anything about the field. An unanswered field listed every
passage too. The list meant "what was read", which read as "what answers". And one
answer could carry only one quote, though `temperature` is stated in three sentences.

## Change

- **Passage reader** (`src/router/passage_reader.py`): an answer carries `quotes`, a list.
  The prompt asks for the whole sentence that states the value, and another only if the
  passage states the value again. An answer is grounded only if **every** quote is
  found. A lone `"quote"` string is still read, as a list of one.
- **`find_quote`**: `locate_quote`, forgiving a full stop the model added where it cut a
  sentence short. Measured, it was the usual reason a real sentence was not found
  ("…Sydney, Australia)." where the text goes on "…Sydney, Australia) and maintained…").
  Used for both the referee's grounding and the router's citations, so they agree.
- **Routing** (`_settle_read`, `route.py`):
  - every passage the reader says states the field is kept, and one read that states
    nothing is dropped;
  - each located quote becomes a candidate of its own; a passage none of whose quotes
    were found stays at full width;
  - when any passage is grounded, ungrounded ones are dropped; then confidence, then
    position order the rest.
- **`FieldRouting`**: `judge_quote` → `judge_quotes`; `citation` → `citations`, aligned
  with the quotes (`None` where one was not found).
- **Unanswered fields carry no candidates.** The note says the judges refused the field;
  what they were shown is on the plan.
- **Eval**: "cited correctly" counts a field when any of its quotes holds the labeled
  evidence; the report says how many fields cited more than one quote.

## Measured (`sharetrait_basic_no_trait__TRADAT031`, all readmes, 8 workers)

| | before | after | after, old one-quote prompt |
|---|---|---|---|
| precision@1 | 8/11 | 8/11 | 8/11 |
| over-answered | 1/15 | **2/15** | 1/15 |
| accuracy | 22/26 | **21/26** | 22/26 |
| cited correctly | 6/7 | 6/7 | 6/7 |
| quotes not found | 1 | 1 | 1 |
| fields citing more than one quote | — | 2 | 1 |

- `temperature` now cites all three sentences giving 21.5 ± 1 °C (acclimation tanks,
  flume, respirometer baths).
- **The multi-quote prompt costs one field.** `oxygen` (labeled NONE) is answered from
  "Water in the flume was constantly aerated…", and some quotes are shorter
  ("Three hundred juvenile spangled perch (L. unicolor;"). With the old one-quote prompt
  and everything else the same, both go away — so the cause is the wording, not the
  routing code. That prompt still gets several answers across passages, but only one
  sentence per passage.
- Before `find_quote`, the multi-quote prompt left 3 fields with a quote not found.
