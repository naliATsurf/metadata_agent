# Router evaluation

Hand-labeled ground truth for the field router, and the scorer that grades a router
configuration against it. One directory of labels per `<standard>__<bundle>` pair.

```bash
python -m eval sheet     # write a labeling sheet — once per bundle
python -m eval score     # grade the current router — every time it changes
make eval                # the same score run
```

Both accept `--bundle`, `--standard`, `--candidates`, and the router flags below.

## Layout

| Path | What | Who writes it |
|---|---|---|
| `eval/sheet.py` | generates `labels.csv` + `sources.csv` | run once per bundle |
| `eval/score.py` | metrics and the risk–coverage tables | run on every change |
| `eval/labels.py` | the label vocabulary and `score()` | — |
| `eval/data/<std>__<bundle>/labels.csv` | **the ground truth** | **you**, by hand |
| `eval/data/<std>__<bundle>/sources.csv` | the answer vocabulary | regenerated, derived |

`labels.csv` is never overwritten once it has any answer in it; a rerun writes
`labels.new.csv` beside it. `sources.csv` is derived and safe to regenerate.

## Filling in `answer`

| Value | Means |
|---|---|
| `<table>::<column>` | this column answers the field |
| `tool::<name>` | a tool computes it from the data |
| `doc::<document>` | it is stated in prose in that document |
| `NONE` | **nothing in this bundle answers this field** |
| `a::b \| c::d` | several are equally correct |

Take the refs from the `ref` column of `sources.csv`. `NONE` is not a failure to
label — it is the most valuable label in the sheet, because knowing when to abstain
is the capability being measured.

**Look past the `rank1..rank5` columns.** They show what BM25 retrieved and are a
convenience, not the menu. An answer the router never ranked is exactly what makes
`recall@k` meaningful: a re-ranker — an LLM reader included — can only choose among
retrieved candidates, so a field whose true answer is absent from the ranked set is
unreachable however good the reader is. `sources.csv` marks each ref
`ever_retrieved` yes/no so those cases are easy to spot.

## What `score` reports

- **recall@k** — of the answerable fields, how many have their true answer anywhere
  in the ranked set. The ceiling on any re-ranking strategy. Unaffected by the veto
  or the reader, both of which only reject.
- **precision@1** — how often the answer given is right.
- **over-answered** — of the fields labeled `NONE`, how many were answered anyway.
  The abstention failure, counted directly. **This is the number that moves.**
- **abstentions: veto N, reader N** — which mechanism did the rejecting.
- **risk–coverage per signal** — accuracy among the fields answered, as a function of
  how many are answered, swept over `bm25` score, query-term `coverage`, and rank-1
  `margin`. A signal worth thresholding on is one where accuracy *rises* as coverage
  falls. Flat or falling means the signal carries no information about its own
  reliability — which is what all three do on `sharetrait_basic__TRADAT031`.

Accuracy counts answering a `NONE` field as **wrong**, not as a non-event.
Over-answering is the failure being measured, and a metric that ignores it will
always recommend answering more.

## Comparing configurations

```bash
python -m eval score --no-veto                       # layer 4a off
python -m eval score --llm-candidate-judge                  # layer 4b on
python -m eval score --llm-candidate-judge --no-judge-batch  # judged field by field
python -m eval score --llm-candidate-judge --judge-workers 8 # same, concurrent
```

`--no-judge-batch` is the comparison worth running: grouping fields that share a
candidate list saves round-trips, but fields judged together stop being independent,
and a model shown one passage and nine fields tends to distribute answers among them.
The sheet is how you find out whether that costs anything.
