# Command line

`metadata-agent` ships with the library: one command per stage, because the stages are
run and inspected separately — resolve a bundle once, route it as many times as you like.

```bash
uv pip install -e .           # puts the command on your PATH
metadata-agent                # the commands, and what each does
```

From a checkout you can skip the install and write `python -m src.cli …` everywhere below.

## 1. Describe the columns

```bash
metadata-agent resolve
```

It reports what it was given, then the resolved catalog:

```text
bundle: data/sample/sharetrait_preprocessed/TRADAT031
tables: ['Blood nitrate_nitrite.csv', 'EPOC.csv', …]   dictionaries: ['codebook.csv']
docs: ['readme.txt', 'readme_hard.txt', 'readme_long.txt']   reader: off

  Table                   Column   Prior     Method       Conf   Meaning (and citation)
  Blood nitrate_nitrite   tank     numeric   dictionary   high   Replicate tank identifier
                                                                 ← codebook row 'tank'
…
45/45 columns resolved   methods={'structured_dictionary': 45}   confidence={'high': 45}
```

Read it right to left: the **meaning**, the **citation** it came from, how sure the
resolver is, and **which tier** resolved it — a codebook table beats a glossary beats the
values themselves, and a claim the column's own values contradict is demoted, not kept.

Try taking a source away, which is how you find out what it was carrying:

```bash
metadata-agent resolve --dictionary none     # no codebook: the README's glossary resolves 44/45
metadata-agent resolve --doc none            # no documents: the codebook alone
metadata-agent resolve --bundle mydir        # your own folder
```

Save it, so routing does not resolve again:

```bash
metadata-agent resolve --out catalog.json
```

## 2. Route a standard over it

```bash
metadata-agent route --catalog catalog.json
```

```text
field                       bucket       assurance  source
doi_dataset                 document     low        readme_hard:(1548, 1926)
title_dataset               document     low        readme_hard:(0, 504)
genus_name                  column       low        Blood nitrate_nitrite:pH
…
coverage: 25/26 routed, unanswered=['photoperiod'],
          by_bucket={'document': 8, 'column': 16, 'tool': 1, 'unanswered': 1}

2. Compiled plan (one extraction task per table)
[0] task=extract_document_fields  player=metadata_specialist  topology=debate
    scope=['readme_hard', 'readme_long']  fields=1
```

The **bucket** says where a field's value comes from:

| Bucket | Means |
|---|---|
| `column` | read from a column of a table |
| `tool` | computed from the data, such as a row count or a date range |
| `document` | quoted from a sentence in a document |
| `unanswered` | nothing in this bundle answers it |

**The coverage line is the point of the whole stage.** `25/26 routed` with
`unanswered=['photoperiod']` is a finding, not an error: it says this bundle cannot
answer that field, before anything tries to extract it.

`genus_name → Blood nitrate_nitrite:pH` in the run above is the other thing to notice.
Without a model, routing is lexical — BM25 ranks and rank 1 wins — so a field and a
column that merely share a word get married. That is what the judges are for (step 3).

Vary the routing; it costs nothing:

```bash
metadata-agent route --catalog catalog.json --standard sharetrait_basic
metadata-agent route --catalog catalog.json --candidates 3
metadata-agent route --catalog catalog.json --search-doc readme_long.txt
```

`--search-doc` is not the resolver's `--doc`. That one chose what *described the
columns*; this chooses what is *searched to answer the fields no column answers*. A
bundle carrying three variants of one README routes them as three rival sources unless
you name one.

## 3. See each field's working

```bash
metadata-agent route --catalog catalog.json --debug
```

Per field: the tool chosen and what it was bound to, the candidates, what does not fit on
type or units, the judge's verdict, and every quote with its citation or a red *not
located*. It reads the artifact rather than instrumenting the router — an intermediate
you can only see with a debug flag is an intermediate the artifact should have carried.

## 4. Bring in a model

```bash
metadata-agent resolve --llm-reader --out catalog.json
metadata-agent route --catalog catalog.json --llm-candidate-judge --judge-workers 8
```

- **`--llm-reader`** lets a model read the narrative no codebook covers — and *only* the
  columns nothing else resolved.
- **`--llm-candidate-judge`** turns on the three router judges: a tool matcher, a column
  matcher over the whole catalog, and a passage reader over each passage. Their most
  valuable answer is *none*, which is what stops the lexical over-answering above.
- **`--judge-workers 8`** issues their calls concurrently, usually the largest win on a
  slow endpoint.
- **`--debug`** additionally logs every prompt and raw reply, and surfaces an error that
  would otherwise look like a judge refusing.
- **`--refresh-tool-cache`** re-asks the tool matcher, whose answers are cached in
  `.cache/tool_matcher` because they depend on the standard and the tools, not the bundle.

Which model each of them runs on is `.env`: `LLM_PROVIDER` / `LLM_MODEL`, and per-module
overrides such as `LLM_MODEL_CANDIDATE_JUDGE`. Every role is registered in
`src/llm_roles.py`, and its prompt is published in the
[Prompt reference](../prompts.md).

## 5. The agentic pipeline

Planning, players with tools, a written record:

```bash
metadata-agent generate --source data/biota/biota.csv --topology default
```

See the [Tutorial](../tutorial.md) for what it does and how to configure it.

## Measuring a change

Hand labels, then a grade for any configuration:

```bash
python -m eval sheet                        # writes labels.csv to fill in
python -m eval score --llm-candidate-judge  # grades the current router
```

`eval/readme.md` explains the metrics; over-answering is the one that moves.

## Every flag

The [CLI reference](../cli-reference.md) lists every command, option, default and choice,
generated from the parsers themselves. From a terminal:

```bash
metadata-agent resolve --help
metadata-agent route --help
metadata-agent generate --help
```

The commands are `src/cli/`, and each is a module with its own `build_parser` and `run` —
which is how the app renders a form from a command's flags and calls the same code the
terminal does. What they call is [Pipelines from Python](pipelines.md).
