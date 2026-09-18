# Quickstart

From a folder of files to a plan that says where every metadata field comes from. Runs on
the sample bundle in this repository, and needs no API key until the last section.

## Install

```bash
make uv-setup                 # dependencies
uv pip install -e .           # puts the `metadata-agent` command on your PATH
metadata-agent                # the commands, and what each does
```

From a checkout you can skip the install and write `python -m src.cli …` instead.

## Three commands

```bash
metadata-agent resolve --out catalog.json      # what does each column mean?
metadata-agent route --catalog catalog.json    # what answers each field of a standard?
metadata-agent generate --source data/biota/biota.csv   # the agentic pipeline
```

**`resolve`** prints one row per column: the meaning it found, where it found it (a
codebook row, a glossary entry, the values themselves), a confidence and a citation.

**`route`** prints one row per schema field, naming a **bucket** — where the value comes
from:

| Bucket | Means |
|---|---|
| `column` | read from a column of a table |
| `tool` | computed from the data, such as a row count |
| `document` | quoted from a sentence in a document |
| `unanswered` | nothing in this bundle answers it |

Read the coverage line at the end first: `25/26 routed`, and which fields nothing can
answer. Routing is cheap, so vary it — `--standard`, `--search-doc`, `--debug` for each
field's working. Every flag is in the [command line tutorial](tutorials/cli.md).

## The same from Python

```python
from pathlib import Path
from src.pipelines import resolve_directory, route, route_and_compile

resolved = resolve_directory(Path("data/sample/sharetrait_preprocessed/TRADAT031"))
field_plan = route(resolved, "sharetrait_basic_no_trait")

field_plan.coverage()
# {'total': 26, 'routed': 25, 'by_bucket': {'column': 16, 'document': 8, 'tool': 1, ...}}
```

Each stage stands alone, so stop where you like: after `resolve_directory` for described
columns, after `route` for coverage, or take the executable plan too with
`route_and_compile(resolved, "sharetrait_basic_no_trait")`.

Read the artifacts:

```python
column = resolved.catalog.get("pH")
column.description, column.link_method, column.link_evidence
# ('Acclimation pH treatment level', 'structured_dictionary', "codebook row 'pH'")

routing = field_plan.routings["temperature"]
routing.bucket, routing.assurance     # ('column', 'high')
routing.judge_quotes, routing.citations   # the sentences quoted, and where they are
routing.mismatches, routing.varies        # what the router flagged about the answer
```

Save a resolution and route it later, the way the two commands do:

```python
resolved.save(Path("catalog.json"))
```

## Add a model

Without one, routing is lexical: BM25 ranks and rank 1 wins, which over-answers, because
a schema and a dataset are written by different people. With one, a model decides what
answers each field — or that nothing does.

```bash
metadata-agent resolve --llm-reader --out catalog.json
metadata-agent route --catalog catalog.json --llm-candidate-judge --judge-workers 8
```

```python
from src.pipelines import build_judges, build_readers

resolved = resolve_directory(Path("mydir"), readers=build_readers())
field_plan = route(resolved, "sharetrait_basic", judges=build_judges(workers=8))
```

Set the provider and model in `.env` (`LLM_PROVIDER`, `LLM_MODEL`, and per-module
overrides such as `LLM_MODEL_CANDIDATE_JUDGE`). Every role a model plays is listed in
`src/llm_roles.py`, and its prompt is published in the [Prompt reference](prompts.md).

## Next

- [Tutorials](tutorials/index.md) — the command line, the pipelines, the modules, extending
- [CLI reference](cli-reference.md) — every command, option and default
- `examples/describe_columns.py` — one stage, and what its artifact carries
- `examples/route_with_your_own_judge.py` — routing with a judge that is code, not a model
- `python -m eval score` — grade a router configuration against hand labels (`eval/readme.md`)
- `make demo` — the whole thing in a browser
- [Tutorial](tutorial.md) — the agentic pipeline: plan, execute, write a record
