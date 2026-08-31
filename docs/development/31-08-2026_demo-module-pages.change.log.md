# Change log 2026-08-31 — Demo: module pages over the example scripts

**Goal:** Give each module a GUI, so what the project can do is showable without
a terminal. The demo had one page — the end-to-end pipeline — while the layers
underneath it (catalog resolution, routing, provenance) were visible only by
running an `examples/` script and reading Rich output. This lands the mechanism
for turning any example into a page, and the first module built on it: the
catalog resolver.

The design constraint was **no second copy of anything**. An example already
states its arguments, their defaults, and their help text to `argparse`, and
already knows how to do the work. A page that restates any of that drifts from
it. So the page derives everything and supplies only what a browser can do
better.

## The example contract

Three functions, and the demo needs nothing else:

- `build_parser() -> ArgumentParser` — the argument surface, separate from parsing
- `run(args, console) -> result` — the work, written through an **injected**
  console, returning what it built
- `main()` — `run(build_parser().parse_args(), Console())`

`examples/resolve_catalog.py` was refactored to this shape. `main()` is
behaviourally unchanged, so the CLI is exactly what it was; the two additions are
the seams the UI needs. The injected console is what lets a caller pass
`Console(record=True)` and capture a run instead of printing it — the module
docstring of `src/router/display.py` already pointed at this. The return value is
what lets a caller render the catalog itself rather than read the printed table.

## The generic form

`demo/components/arg_form.py`. `render_form(parser)` walks `parser._actions` and
maps each to a widget: `store_true` → checkbox, `append` → one-value-per-line
text area, `choices` → selectbox, anything else → a text input passed through the
action's own `type` callable (so `type=Path` still yields a `Path`). Each
argument's `help=` becomes the widget's tooltip. It returns the same `Namespace`
`run()` would have received from the command line.

The result is that **adding a flag to an example grows a control on its page**,
with no demo-side change. `--prose-reader`, `--llm-reader`, `--doc`, and
`--debug` all became working controls without a widget being written for any of
them.

`command_line(parser, args)` renders the equivalent invocation — only values that
differ from the defaults, paths shortened against the cwd — so the page shows
`python examples/resolve_catalog.py --bundle data/tests/router_test` above the
Run button. The GUI stays legible as a thing that *stands for* a command.

A per-argument `overrides` hook covers the cases a generic text box serves badly.
The catalog page uses exactly one: a bundle picker that scans `data/` for
directories containing CSVs.

## Rendering: terminal first, then native

Two passes, and the first is worth recording because it was wrong in an
instructive way.

**Pass one** displayed the run verbatim: `Console(record=True)` →
`export_html(theme=MONOKAI)` → `st.html`. This is the cheapest possible
faithfulness — the Rich table, the conflicts section, the colour coding are
literally what `render_catalog` draws, one renderer serving both terminal and
browser. It also meant the planned split of `display.py` into a
presentation-neutral view model plus a Rich adapter was **unnecessary**, and that
work was dropped.

It was also not what a browser is for. A `<pre>` block of box-drawing characters
is a screenshot of a terminal, not a UI.

**Pass two** added `demo/components/catalog_view.py`: a metric row (resolved
count, high-confidence count, conflicts, methods used), a sortable table of the
resolved columns, filters over it (free-text search on name and meaning, table
and method multiselects, an "unresolved only" toggle), and the contested columns
broken out below as expanders — conflicts as error callouts, corroboration as
success callouts, losing candidates in their own table. The filters are the part
that earns the browser: on the 45-column TRADAT031 bundle, "unresolved only" is
how you would actually read the result.

Both renderers read `ResolvedColumn` directly, which is already a clean frozen
data type — so there is still no view-model layer and no second copy of the
catalog's meaning, only a second presentation. The Rich output is kept in a
collapsed "Terminal output" expander, with a plain-text download.

`run_example` therefore takes an optional `render` callback. Given one, it
renders the result natively and demotes the console; without one, the console is
all there is. **A new example page gets the terminal view for free and can grow a
native renderer later without restructuring.**

## Navigation

`demo_app.py` stays the entry point (the makefile and `docker-compose.yml`
hardcode it) and is now a shim over `demo/app.py`, which owns `set_page_config`
and `st.navigation`. `set_page_config` moved out of the generation page — it must
be called exactly once, in the entry point, before navigation.

Two nav entries, deliberately:

- **Metadata generation** — the existing pipeline page, the default landing page,
  unchanged apart from losing `set_page_config`.
- **Modules** — `demo/pages/modules.py`, a host page holding a `MODULES` registry
  of label → render function. Adding a module is one line. The picker
  (`st.segmented_control`) appears only when more than one is registered, so
  today the page goes straight to the catalog resolver rather than showing a
  one-option control.

## Changes in `src`

One, and it is small: `_METHOD_ABBR` in `src/router/display.py` became public
`METHOD_LABELS`, exported from `src.router`. The browser and the terminal now
spell "dictionary" / "prose·read" from the same table instead of each keeping a
copy.

## Verified

- `examples/resolve_catalog.py` unchanged from the command line: `--help` and a
  full run against `data/tests/router_test` produce what they did before.
- The catalog page driven headlessly through Streamlit's `AppTest`: form renders
  from the parser, Run executes, `data/tests/router_test` gives 8/8 resolved with
  citations and surfaces the codebook's Kelvin trap ("claimed units Kelvin, but
  values 4.1–21.9 lie in the Celsius range") as a conflict; TRADAT031 gives 45
  rows with the Table column present (it appears only for multi-table bundles); a
  non-existent bundle surfaces as a clean error, not a traceback.
- The module picker confirmed with two modules registered: both options list and
  switching swaps the body.
- All three routes serve 200 against a live server. `python -m unittest discover
  -s tests` → 218 pass.

Two bugs found during that and fixed: `export_html` clears the record buffer by
default, so the text download was producing an empty file (`clear=False`); and
the recording console was also writing to the server's stdout (`file=StringIO()`).

## Scope and limits

- **The examples are now load-bearing, and nothing tests that.** They were
  throwaway scripts; they are now the demo's API. Breaking `run()`'s signature
  breaks the UI silently — no test imports `examples`. One small test per example
  asserting `build_parser()` and `run()` exist and are callable is the obvious
  insurance and is not here.
- **`render_form` reads `parser._actions`.** Semi-private, stable in practice,
  and widely relied on — but private. The alternative is a declarative spec per
  example feeding both argparse and the form: more code, no drift risk. Not taken.
- **Import of an example must stay side-effect free.** Importing
  `examples.resolve_catalog` runs its module body, including its
  `sys.path.insert`. Harmless today; an example doing real work at import time
  would do it on every page load.
- **`examples/` is not in the installed package.** `pyproject.toml` has
  `include = ["src", "src.*"]`. The Docker image works because it does `COPY . .`
  and runs from `/app`, so the import resolves off the filesystem. The demo
  depends on being run from a checkout, not on the installed distribution.
- **No deep links to a module.** The module choice lives in session state, not
  the route, so `/modules` always opens on the first one. `st.query_params`
  driving the selection is the fix.
- **Runs are blocking.** `run_example` executes inline under a spinner. Adequate
  for the deterministic tiers; `--llm-reader` on a large bundle will hold the
  script. The pipeline page's spawn-process-and-poll machinery
  (`demo/pages/metadata_generation.py`) is the pattern to extract if a module
  ever needs to be interruptible — it was deliberately not extracted here,
  because nothing yet needs it.
- **No replay mode.** Every run is live. `data/sample_output/` already holds
  recorded `catalog.yaml` / `field_plan.yaml` / `compiled_plan.yaml`, and a
  fixture-backed default would make the pages demonstrable without API keys or
  wait time. Deferred.
- **One module registered.** The field router (`examples/field_router_plan.py`)
  and the standard printer (`examples/print_standard.py`) share the same argparse
  shape and are the obvious next two; each needs the `build_parser`/`run` split
  plus a registry line, and a native renderer only if the console view proves
  insufficient.

## Unrelated, found in passing

- **`uv.lock` is corrupt at HEAD.** Around line 948 the `iniconfig` wheels array
  is unterminated and an `importlib-metadata` block is spliced in without its
  `[[package]]` header. This breaks `uv sync`, and with it `make demo`, `make ci`,
  and the Docker build. `uv sync` **exits 0** while printing the parse error,
  which is how it went unnoticed. Not fixed here — regenerating the lock is a
  dependency decision. The demo was run from `.venv` directly.
- `ruff check .` reports one pre-existing failure: unused `EvidenceRef` import in
  `tests/test_searchable.py`. Auto-fixable, untouched.
