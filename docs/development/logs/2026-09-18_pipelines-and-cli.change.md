# Change log 2026-09-18 — Pipelines and the command line move into the library

**Goal:** `examples/field_router_plan.py` had stopped being an example. It assembled the
field-driven path, built the judges, held the default standard and the tool cache path,
and three other places imported it: the app's two pages, the eval harness, and the tests.
`examples/resolve_catalog.py` was the same for layer 3. So the wiring of the pipeline
lived outside the library, and a change made to improve a demo could quietly change what
the eval measured.

Two things followed from that. There was no way to run *part* of a path from code
without going through argparse, and the shipped console script pointed at
`src.main` — the agentic entry — with nothing for the field-driven one.

## Change

### `src/pipelines/` — how the layers are put together, and nothing else

A layer owns an algorithm; a pipeline module owns an order. Anything here that starts
deciding rather than composing belongs in a layer.

| Module | Holds |
|---|---|
| `catalog` | `resolve`, `resolve_directory`, `DEFAULT_BUNDLE` — bundle files → a resolved catalog |
| `routing` | `route`, `compile_plan`, `route_and_compile`, `DEFAULT_STANDARD` |
| `field_driven` | `run` and `FieldDrivenRun` — the whole path, composed from the stages |
| `models` | `Readers`, `Judges`, `build_readers`, `build_judges`, `invoker`, `TOOL_MATCH_CACHE` |

Each stage is importable on its own, so a caller that wants only described columns, or
only coverage, runs that stage and stops.

`models.invoker` builds the `prompt -> text` callable from a module's configured model,
lazily, and takes an optional `log(kind, text)` callback instead of a console — which is
how `--debug` prints prompts without a terminal reaching into a pipeline, and why the
deterministic path still needs no provider SDK.

### `src/cli/` — the command line, shipped with the library

`metadata-agent <command>`, one command per stage: `resolve`, `route`, and `generate`
(wrapping `src.main` until it gets the same treatment). `python -m src.cli` runs it from
a checkout. `[project.scripts]` now points at `src.cli:main`.

Each command keeps its own `build_parser` and `run`, which is what lets the app render a
form from a command's flags and call the same code the terminal does. Shared flags —
the model options, the judge options — are defined once in `src/cli/options.py` and used
by both the route command and the eval, so a flag cannot drift between what is run and
what is graded. Printing lives in `src/cli/display.py`.

### The callers

- `demo/pages/*` import `src.cli.resolve` / `src.cli.route`, and the command shown above
  a run now reads `metadata-agent resolve …`.
- `eval/cli.py` imports the stage (`route`) and the shared judge options.
- `examples/resolve_catalog.py` and `examples/field_router_plan.py` are **deleted**. As
  wrappers around the command they demonstrated nothing; two examples replace them and
  show what a flag cannot:
  - `describe_columns.py` runs one stage and prints what it produced — every column's
    meaning with its tier, confidence and citation, the corroboration behind one, the
    enriched search reaching `ucrit`, and the glossary resolving the same columns once
    the codebook is removed;
  - `route_with_your_own_judge.py` routes with a column matcher written in code and a
    passage reader whose model call is a stub, which are the two seams every LLM role
    hangs on. `tests/test_examples.py` runs both.
- **`docs/cli-reference.md` is generated** from the commands' own parsers
  (`docs/_ext/clidocs.py`, alongside the prompt reference): every command, option group,
  default, choice and help string, so a renamed flag cannot leave a stale entry behind.
  `src/main.py` gained a `build_parser()` so `generate` is documented the same way, and
  `tests/test_cli.py` fails when a flag is missing from the page.
- **Docs**: `docs/quickstart.md` is the five-minute way in, and `docs/tutorials/` is the
  series behind it — `cli` (the commands, with their real output), `pipelines` (the same
  paths as functions, and the artifacts they return), `modules` (calling a layer directly:
  contexts, catalog, schema, routing, judges, compiler, tools), `extending` (a standard, a
  tool, a judge, a model, a threshold). Every snippet on those pages was run before it was
  written. The existing `docs/tutorial.md` (the agentic pipeline) is untouched.
