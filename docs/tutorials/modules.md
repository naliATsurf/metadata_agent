# Working with the modules

Under every pipeline is a layer that owns one algorithm. This page calls those layers
directly — which is what you do when you are building on one of them rather than running
the path.

| Layer | Module | Owns |
|---|---|---|
| Context | `src.context` | reading tables and documents, and searching them |
| Catalog (3) | `src.router.catalog` | what each column means, on what evidence |
| Schema | `src.router.schema` | a metadata standard flattened to fields |
| Routing (4) | `src.router.route` | which source answers which field |
| Type fit (4a) | `src.router.type_fit` | whether a candidate's type and units fit |
| Judges (4b) | `src.router.tool_matcher`, `column_matcher`, `passage_reader` | a model's verdict, refereed |
| Compiler (5) | `src.router.compile` | the routing as executable tasks |
| Tools | `src.tools` | deterministic computations over a context |

## Contexts: reading a file

A context is one file or folder, read lazily, with a search over it.

```python
from src.context import create_context

table = create_context("data/sample/sharetrait_preprocessed/TRADAT031/growth.csv", name="growth")
table.resources                                   # ['growth']
table.get_resource_info("growth").field_names     # ['nitrate', 'pH', 'tank', …]
table.read_resource("growth", limit=5)            # a DataFrame

doc = create_context(".../readme.txt", name="readme")
doc.read_text("readme")[:80]
[c.start_offset for c in doc.iter_chunks("readme")][:5]   # paragraphs or sections
doc.search("nitrate treatment", k=2)              # EvidenceRefs with (start, end) spans
```

Everything above a context depends on this interface only, which is why a new modality is
a new context class rather than an edit everywhere.

## Catalog: resolving meanings yourself

```python
from src.router import resolve_bundle, resolve_catalog

catalog = resolve_catalog(table, sources=[codebook, readme])       # one table
catalog = resolve_bundle([growth, epoc], sources=[codebook])       # one catalog, many tables
```

`sources` are auto-classified: a table whose values are the data's column names is read as
a codebook, a text file is kept for the reader and scanned for a glossary. Nothing depends
on a filename.

Pass a reader to have a model read the narrative, and a comparer to decide which
differently worded claims mean the same:

```python
from src.router import CachedProseReader, LLMClaimComparer, LLMProseReader

invoke = lambda prompt: my_model(prompt)          # any prompt -> text callable
catalog = resolve_catalog(
    table, sources=[readme],
    prose_reader=CachedProseReader(LLMProseReader(invoke)),
    claim_comparer=LLMClaimComparer(invoke),
)
```

Both are interfaces, not classes to inherit from: implement `read_many` or `group_many`
and you have replaced the model with whatever you like.

## Schema: a standard as fields

```python
from src.router import walk_schema
from src.standards import get_schema_for_standard

schema = get_schema_for_standard("sharetrait_basic_no_trait")
for field in walk_schema(schema)[:3]:
    field.path, field.type, field.required, field.description
# ('doi_dataset', 'str', True, 'dataset DOI, provided in URL')
```

The description is what the router routes on, so it is the part worth writing carefully.

## Routing: the layer itself

`route_fields` is the whole of layer 4, and takes its judges as arguments:

```python
from src.router import route_fields

field_plan = route_fields(
    schema,
    catalog=catalog,
    docs=[readme],          # Searchable contexts
    k=5,                    # candidates per field
    tool_matcher=None,      # layer 4b, all optional
    matcher=None,
    reader=None,
)
```

With none of them it is BM25 and rank 1 wins. With them, nothing is filtered lexically:
the column matcher sees the whole catalog and the passage reader reads every passage.

Grade a candidate yourself:

```python
from src.router import mismatch, mismatches, variations

mismatch(field, column)      # "field asks for a name; days holds numbers (int64)", or None
mismatches(field, routing.candidates, catalog)   # the same, per candidate, as ref — reason
variations(field, routing.candidates, catalog)   # columns that hold more than one value
```

These only ever lower confidence. A blunt rule that removed candidates would hide right
answers, so it grades instead.

## Judges: calling one on its own

Each judge is a seam with a small interface. The column matcher, with the cards it rules
on built from a catalog:

```python
from src.router.column_matcher import LLMColumnMatcher, group_card, merge_columns

cards = [group_card(group) for group in merge_columns(catalog.columns)]
verdicts = LLMColumnMatcher(invoke).match(fields=walk_schema(schema)[:5], cards=cards)
verdicts["doi_dataset"].choice        # 'growth::doi', or None to abstain
verdicts["doi_dataset"].because       # why
verdicts["doi_dataset"].confidence    # high | medium | low
```

The passage reader, one passage at a time:

```python
from src.router.passage_reader import LLMPassageReader, passage_card

card = passage_card(candidate, doc.read_text("readme"))
verdict = LLMPassageReader(invoke).read(fields=[field], passage=card)[field.path]
verdict.quotes, verdict.grounded      # the sentences, and whether all were found
```

Every verdict is refereed by code before the router uses it: a ref that was not shown is
discarded, a quote that cannot be located caps confidence at `low`, a failed call
abstains. `src/llm_roles.py` lists each role with its prompt and what its referee checks.

## Compiler: the routing as tasks

```python
from src.router import compile_field_plan

plan = compile_field_plan(
    field_plan,
    budget=2000,                                   # characters of seeded evidence per task
    bucket_player={"document": "metadata_specialist"},
    assembly_player="metadata_generator",
)
```

Fields answered from the same place and at the same assurance are grouped into one task;
a field nothing answers gets no extraction task but is still named on the assembly task,
so the record nulls it explicitly rather than by accident.

## Tools

```python
import src.tools                                  # importing registers them
from src.tools.base import column_args_of, field_answering_tools, tools_for

[t.name for t in field_answering_tools()]         # tools whose result is a metadata value
column_args_of(...)                               # the columns such a tool must be given
[t.name for t in tools_for(table)]                # what can run against this context
```

A tool declares the capability it needs (`requires=TabularContext`), so gating is a
capability check rather than a table someone maintains.

## The rest

- `src.thresholds` — every tunable number, with its stage and help text
- `src.llm_roles` — every single-call LLM role: question, model setting, referee, prompt
- `src.llm_calls` — `count_llm_calls()`, which counts any model call inside a block

Next: [Extending](extending.md) — your own standard, tool, judge or model.
