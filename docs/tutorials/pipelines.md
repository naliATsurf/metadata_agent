# Pipelines from Python

The commands are thin: they parse flags, call a pipeline, and print. This page calls the
same pipelines directly, which is what you want when the result goes somewhere other than
a terminal.

A pipeline module owns an *order*, never an algorithm. `src/pipelines/` holds four:

| Module | Holds |
|---|---|
| `catalog` | `resolve`, `resolve_directory` — bundle files → a resolved catalog |
| `routing` | `route`, `compile_plan`, `route_and_compile` |
| `field_driven` | `run` — the whole path, composed from the two above |
| `models` | `build_readers`, `build_judges` — the model-backed roles a stage runs with |

## The whole path

```python
from pathlib import Path
from src.pipelines import run

result = run(Path("data/sample/sharetrait_preprocessed/TRADAT031"), "sharetrait_basic_no_trait")
result.resolved      # ResolvedBundle — the catalog and the files it came from
result.field_plan    # FieldPlan — where every field is answered
result.plan          # Plan — the tasks an executor would run
```

## Stage by stage

Which is what you want as soon as one stage is slower or costlier than the others:
resolve once, route many times.

```python
from src.pipelines import compile_plan, resolve_directory, route

resolved = resolve_directory(Path("data/sample/sharetrait_preprocessed/TRADAT031"))
field_plan = route(resolved, "sharetrait_basic_no_trait")
plan = compile_plan(field_plan)
```

Stop wherever the answer is. Described columns come from the first line; coverage from
the second; only an executor needs the third.

Narrow what each stage reads — they read different things for different reasons:

```python
# the resolver: which files may describe the columns
resolved = resolve_directory(bundle, dictionaries=["codebook.csv"], documents=["none"])

# the router: which of the resolution's documents are searched to answer fields
field_plan = route(resolved, "sharetrait_basic_no_trait",
                   documents=[p for p in resolved.documents if p.name == "readme.txt"])
```

Resolve once, save, route later — exactly what the two commands do between runs:

```python
resolved.save(Path("catalog.json"))

from src.router import ResolvedBundle
again = ResolvedBundle.load(Path("catalog.json"))
```

## Reading the artifacts

### The resolution

```python
catalog = resolved.catalog
len(catalog.columns)                      # 45

column = catalog.get("ucrit")
column.description                        # 'Critical swimming speed (Ucrit)'
column.units                              # 'cm s-1'
column.link_method, column.link_confidence  # ('structured_dictionary', 'high')
column.link_evidence                      # "codebook row 'ucrit'"
column.corroborated_by                    # citations of sources that agreed
column.distinct_count, column.distinct_values   # what the values look like

catalog.search("critical swimming speed", k=3)   # the router's own search, enriched
catalog.conflicts                                # claims the sources disagreed on
```

`catalog.find(name, resource)` disambiguates a column name that occurs in two tables.

### The field plan

```python
field_plan.coverage()
# {'total': 26, 'routed': 25, 'unanswered': ['photoperiod'],
#  'by_bucket': {'document': 8, 'column': 16, 'tool': 1, 'unanswered': 1},
#  'by_assurance': {...}}

field_plan.unanswered()     # ['photoperiod'] — the finding, before extraction
field_plan.judged           # did judges decide this plan, or BM25?
field_plan.to_dict()        # the whole artifact, JSON-friendly
```

One field:

```python
routing = field_plan.routings["sample_size"]
routing.bucket, routing.assurance        # ('tool', 'high')
# The judge_* and tool_* fields are filled when judges ran; without them they are empty
# and the candidates are BM25's ranking.
routing.candidates                       # EvidenceRefs: where the value may be read
routing.judge_choice, routing.judge_note # what a judge picked, and why
routing.judge_quotes, routing.citations  # sentences quoted, and where they are
routing.judge_grounded                   # was every quote found in the source?
routing.tool_choice, routing.tool_arguments   # the tool, and the table/columns bound to it
routing.mismatches, routing.varies       # type or unit problems; a column that varies
```

### The compiled plan

```python
for task in plan.steps:
    task.task, task.player, task.topology      # what to do, who does it, how
    task.target_resources                      # the files it opens
    task.fields                                # the schema fields it must fill
    task.field_bindings                        # per field: its candidates, and tool arguments
```

## Adding a model

Model-backed roles are built separately and handed in, so no stage reaches for a model on
its own and the deterministic path never needs a key.

```python
from src.pipelines import build_judges, build_readers

resolved = resolve_directory(bundle, readers=build_readers())        # layer 3 reads prose
field_plan = route(resolved, "sharetrait_basic_no_trait",
                   judges=build_judges(workers=8))                   # layer 4b judges
```

Both take the same shape of options:

```python
build_judges(
    enabled=True,        # False gives you the deterministic path back
    batch=True,          # False asks about one field per call
    workers=8,           # concurrent calls
    refresh_tool_cache=False,
    provider="openai", model="gpt-4o-mini", temperature=0.0,   # else the .env settings
    log=lambda kind, text: print(kind, text[:200]),            # every prompt and reply
)
```

Count what a run cost:

```python
from src.llm_calls import count_llm_calls

with count_llm_calls() as calls:
    field_plan = route(resolved, "sharetrait_basic_no_trait", judges=build_judges())
print(calls.calls, "model calls")
```

## Tuning the numbers

Passage sizes, batch sizes, sampling and the resolver's cut-offs live in
`src/thresholds.py`, and are read when a value is needed rather than at import. Set one in
`.env` as `THRESHOLD_<NAME>`, or scope it to a block:

```python
from dataclasses import replace
from src import thresholds

with thresholds.use(replace(thresholds.current(), router_max_fields_per_call=10)):
    field_plan = route(resolved, "sharetrait_basic_no_trait", judges=build_judges())
```

`thresholds.specs()` lists every threshold with its stage, label and help — it is what the
app's settings panel renders.

## A worked example

`examples/describe_columns.py` runs the catalog stage and prints everything above for the
sample bundle: the resolved table, the evidence behind a meaning, the enriched search, and
what changes when the codebook is taken away.

```bash
python examples/describe_columns.py
```

Next: [Working with the modules](modules.md), for what happens inside a stage.
