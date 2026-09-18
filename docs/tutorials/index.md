# Tutorials

Four ways in, depending on how deep you are going. Each page is runnable against the
sample bundle in this repository, and every snippet is code that has been run.

```{toctree}
:maxdepth: 1

cli
pipelines
modules
extending
```

| Page | For |
|---|---|
| [Command line](cli.md) | running the ready-made pipelines from a terminal, and reading what they print |
| [Pipelines from Python](pipelines.md) | the same pipelines as functions: whole paths, single stages, the artifacts they return |
| [Working with the modules](modules.md) | going under a stage — contexts, the catalog, the router, the judges, the compiler |
| [Extending](extending.md) | your own standard, tool, judge, or model |

Looking for a specific flag? The [CLI reference](../cli-reference.md) lists every
command, option and default, generated from the parsers.

New here? [Quickstart](../quickstart.md) is the five-minute version. For the agentic
pipeline — an orchestrator planning, players executing — see the
[Tutorial](../tutorial.md).

## What the pipeline is, in one table

A **bundle** is a folder: data tables, plus the files that explain them (a codebook, a
README, a paper). Three stages turn it into a plan, and each runs on its own.

| Stage | Question | Module | Artifact |
|---|---|---|---|
| Catalog resolution | what does each column mean? | `src.pipelines.catalog` | `ResolvedBundle` |
| Field routing | what answers each field of a standard? | `src.pipelines.routing` | `FieldPlan` |
| Compilation | who extracts what, from where? | `src.pipelines.routing` | `Plan` |

The artifact to read is the **field plan**: it says where every field's value comes from,
with the evidence, and which fields nothing in the bundle can answer — before any
extraction runs.
