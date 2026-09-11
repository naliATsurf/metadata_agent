# Change log 2026-09-11 — One settings panel for the whole pipeline

**Goal:** Make the pipeline's parameters answerable in the app. Which model plans,
which model reads a column's meaning out of prose, how many candidates the router
keeps per field, whether players may call tools — all of it lived in `.env` and in
per-example flags, so the deployed demo could only ever run the one configuration
its process started with, and the same question ("which model?") was asked once per
page with no way to answer it in both.

The panel lives on the landing page, above the run button: what to describe, how to
describe it, then go.

## Configuration is read when it is asked for

`src/config.py` captured its environment at import time — `LLM_PROVIDER`,
`DEFAULT_MODEL`, `PLAYER_TOOL_EXECUTION_MODE`, `PLAYER_MAX_TOOL_ITERATIONS`,
`PLANNING_TEMPERATURE`, `PLAYER_TEMPERATURE` were module constants, and
`src/players/player.py` had imported two of them into its own namespace besides.
A constant captured at import cannot be overridden by anything that happens later,
which is exactly what a settings panel needs to do.

They are functions now — `default_provider()`, `default_model()`,
`player_tool_execution_mode()`, `player_max_tool_iterations()`,
`module_default_temperature()` — each reading the variable at the moment it is
needed. `llm_settings()` already worked this way for the per-module variables; the
globals now match it. Nothing about the `.env` surface changed: the same variables
with the same names and the same defaults.

`LLM_MODULES` became a mapping to `LLMModule(label, temperature, description)`. The
label and the blurb describe the module, not a rendering of it, so `--help`, the
settings panel, and a configuration summary can all draw on the same two sentences —
and a module added to that dict grows a row in the panel without touching the UI.

## The settings themselves

`demo/settings.py` holds `PipelineSettings`: a model (provider, model, temperature)
for each of the four modules that call one, the topology, the players' tool budget,
the catalog's prose tier, and the router's candidate budget and field reader. It is
a value, not global state — a run is *handed* the settings it should use:

| stage | reached by | given the settings as |
| --- | --- | --- |
| generation | a spawned child process | `environment()`, applied for the call by `_configured()` and restored after |
| catalog resolver page | `resolve_catalog.run()` in-process | `catalog_arguments()`, as the form's defaults |
| field router page | `field_router_plan.run()` in-process | `router_arguments()`, likewise |

Nothing is applied process-wide, so two pages disagreeing is a visible override
rather than a hidden one — the command line printed beside each form still
reproduces exactly what that page would run.

`token()` is a short digest of the whole value. Two things key off it: a cached
result, which belongs to the settings that produced it (the same file under a
different model is a different run, and showing the old result for it would be a
lie), and the widgets whose defaults these settings are. That second use is what
`arg_form.Defaults` exists for: Streamlit remembers a widget by its key, so a widget
whose default changed after it was first drawn keeps showing the old value. The keys
of *overridden* arguments carry the token, so they become new widgets when the
settings behind them change while the rest of the page keeps its state.

## The field router can now read narrative prose

The panel offers one catalog prose tier, and the router resolves a catalog before it
routes — but `examples/field_router_plan.py` only had the deterministic tier, so a
third of that setting had nowhere to go. It gained `--llm-reader` and a
`--catalog-provider` / `--catalog-model` / `--catalog-temperature` group, drawing on
the same `CATALOG_RESOLVER` configuration as `examples/resolve_catalog.py`: it is the
same stage, so it is the same model.

A form with six argument groups then needed somewhere to put them, and
`example_runner` laid groups out in a single row. It wraps at three per row now,
balancing the last row against the one before it.

## Not done

The generation pipeline still plans with an LLM rather than routing fields, so the
catalog and routing settings govern the module pages alone. When layers 3–5 are
wired into generation they will already have their controls; nothing in the panel
assumes which path consumes them.
