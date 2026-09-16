# Change log 2026-09-16 — A document routing carries the passage it cited, not the file's name

**Goal:** Stop a document field's routing from collapsing to a file name. The judge was
shown `card["text"] = candidate.snippet`, and a snippet is a **200-character preview** by
design while a retrieved chunk runs to 2 000 or more. On `TRADAT031` the acclimation
paragraph is 1 000 characters and the answers sit at offsets 599–738 — so `21.5 ± 1°C`,
`12:12-h light–dark cycle` and `bloodworms` were retrieved and then withheld. The judge
abstained on six fields that *are* answered in the material it was handed, which from the
outside is indistinguishable from a judge that read the passage and found nothing.

Two further collapses sat behind it. `promote` matches on `candidate_ref`, and every span
of one document shares `doc::<resource>`, so a correct verdict could not say *which*
passage it meant and rank 1 fell back to BM25 order. And `Verdict.quote` — the sentence
the judge cited, already validated against the card — was computed, graded, and dropped.

## Change

- **`describe(candidate, catalog, passage=None)`** (`src/router/judge.py`) takes the full
  text the span points at and uses it as the card, falling back to the snippet.
- **`_passage_reader(docs)`** (`src/router/route.py`) dereferences a `quoted_span` back to
  `text[start:end]`. Returns `None` for structured candidates — a column's card is what
  layer 3 resolved, not a slice of a document.
- **`promote(candidates, verdict, passage=None)`** breaks a same-ref tie with the judge's
  own quote, so the span it actually cited leads.
- **`FieldRouting.judge_quote`** carries that quote into the persisted plan (`to_dict`
  included). For a document field it is the nearest thing to the answer the router holds,
  and re-deriving it downstream means re-reading the whole document.
- **`_locatable`** now delegates to a shared `_contains`; behaviour unchanged.

## Evaluation

- **`evidence` column** in the labeling sheet (`eval/sheet.py`, `eval/labels.py`): a few
  words the correct quote must contain. A ref alone cannot grade a document answer —
  `doc::readme_long` is one label for 34 000 characters, so a routing citing the lab bench
  temperature and one citing the acclimation temperature score identically. Optional per
  field; unlabeled fields are not span-scored.
- **`cited correctly N/M`** and an ungrounded-quote warning in `eval/score.py`, and the
  quote itself as a column in *Where rank 1 is wrong* — two routings naming the same file
  are told apart only by what they cited.

## Measured (`sharetrait_basic_no_trait__TRADAT031`, judge on)

| | before | after |
|---|---|---|
| precision@1 | 3/12 | 5/12 |
| accuracy | 20/30 | 21/30 |
| abstained with an answer present | 8 | 6 |

`temperature` and `duration` flipped from abstention to a correct, grounded citation. The
span metric immediately earned itself: `temperature` had been scoring as a document-level
**hit** while quoting *"Samples were analysed at 22°C."* — the bench, not the tank — with
`grounded=False` already flagging it. Conversely `title_dataset` scored a **miss** while
quoting the right sentence, because `readme_long.txt` has the readme's text appended
inside it. Both directions of error were invisible at file granularity.

## Not fixed

`photoperiod` retrieves **nothing**: the word never appears in the text, only `12:12-h
light–dark cycle`. Four more fields (`species_reported`, `food_type`,
`life_stage_general`, `representative_stage`) are answered in chunk 0 — the paragraph that
answers six fields — which BM25 never ranks for any of them. The remaining failure is
retrieval, not judgement, and the size-gated whole-document path (`TODO(long-doc)`,
mirroring `_WHOLE_DOC_MAX_CHARS` in layer 3) is the intended fix.
