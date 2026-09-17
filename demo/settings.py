"""Every configurable parameter of the pipeline, as one value.

The pipeline is several stages deep and each stage has knobs: which model plans,
which model reads a column's meaning out of prose, how many candidates the router
keeps per field, whether players may call tools. Those answers lived in ``.env`` and
in per-example flags, which meant the app could only ever run the configuration its
process started with, and that the same question ("which model?") was asked once per
page.

They are gathered here instead, as :class:`PipelineSettings`. The landing page
renders it with :func:`render`; every other page reads the same value through
:func:`current` and starts its form from it. Nothing is applied globally — a run is
handed the settings it should use, so two pages disagreeing is a visible override
rather than a hidden one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import streamlit as st

from src.config import (
    DEFAULT_TOPOLOGY,
    LLM_MODULES,
    PLAYER_TOOL_MODES,
    PROVIDER_CONFIGS,
    LLMSettings,
    llm_settings,
    player_max_tool_iterations,
    player_tool_execution_mode,
)
from src import thresholds as threshold_registry
from src.thresholds import Thresholds, env_name
from src.topology import EXECUTION_TOPOLOGIES


#: The prose tiers layer 3 can run above the codebook lookup and the value prior,
#: in increasing order of what they can read — and of what they cost.
PROSE_TIERS = {
    "off": "Codebooks — tables, or glossaries in a README — and the value prior. No model call.",
    "llm": "Also reads the narrative no codebook covers, with the catalog reader's model.",
}

#: Where the panel's widgets keep their state.
_WIDGET_PREFIX = "pipeline"

#: Where the assembled settings are published for the other pages to read.
_SESSION_KEY = "pipeline.settings"

#: Shown under a module page's command line, so it is clear where its form started.
FORM_NOTE = (
    "Defaults come from the pipeline settings on the Metadata generation page. "
    "Changing a control here overrides them for this run only."
)


@dataclass(frozen=True)
class PipelineSettings:
    """What the whole pipeline should run as.

    Args:
        models: The provider, model, and temperature for each module that calls
            one, keyed as :data:`~src.config.LLM_MODULES` keys them.
        topology: Execution topology for plan execution.
        player_tool_mode: Whether players may call tools the model must supply
            arguments for, or only the deterministic survey.
        player_tool_iterations: Max model↔tool rounds per task while investigating.
        catalog_prose_tier: Which prose tier layer 3 runs; a key of
            :data:`PROSE_TIERS`.
        catalog_debug: Log every prompt and raw response of the prose reader.
        router_candidates: How many ranked candidates the router keeps per field.
        router_candidate_judge: Let a model adjudicate the candidates (layer 4b).
        router_judge_workers: How many of the judge's calls to issue at once.
        router_judge_batch: Ask about many fields per call, rather than one.
        thresholds: The numbers the catalog resolver, router and compiler decide by
            (:mod:`src.thresholds`).
    """

    models: Mapping[str, LLMSettings] = field(default_factory=dict)
    topology: str = DEFAULT_TOPOLOGY
    player_tool_mode: str = "investigate"
    player_tool_iterations: int = 8
    catalog_prose_tier: str = "off"
    catalog_debug: bool = False
    router_candidates: int = 5
    router_candidate_judge: bool = False
    router_judge_workers: int = 1
    router_judge_batch: bool = True
    thresholds: Thresholds = field(default_factory=Thresholds.from_environment)

    # -- what a run is given ------------------------------------------------

    def environment(self) -> dict[str, str]:
        """These settings as the environment variables :mod:`src.config` reads.

        The generation workflow runs in its own process and configures itself from
        the environment, so this is how a choice made in the browser reaches it.
        Every value is set explicitly, including the ones matching ``.env``: a
        child process must not inherit a stale answer for a question the panel asks.
        """
        env: dict[str, str] = {}
        for module, settings in self.models.items():
            env[f"LLM_PROVIDER_{module}"] = settings.provider
            env[f"LLM_MODEL_{module}"] = settings.model
            env[f"LLM_TEMPERATURE_{module}"] = str(settings.temperature)
        env["PLAYER_TOOL_EXECUTION_MODE"] = self.player_tool_mode
        env["PLAYER_MAX_TOOL_ITERATIONS"] = str(self.player_tool_iterations)
        env.update(self.thresholds.environment())
        return env

    def catalog_arguments(self) -> dict[str, Any]:
        """Starting values for ``examples/resolve_catalog.py``'s form, by ``dest``."""
        model = self.model_for("CATALOG_RESOLVER")
        return {
            "llm_reader": self.catalog_prose_tier == "llm",
            # There are no prompts to log unless a model is the one reading.
            "debug": self.catalog_debug and self.catalog_prose_tier == "llm",
            "provider": model.provider,
            "model": model.model,
            "temperature": model.temperature,
        }

    def router_arguments(self) -> dict[str, Any]:
        """Starting values for ``examples/field_router_plan.py``'s form, by ``dest``.

        The router routes a catalog the resolver page already resolved, so it takes
        only its own settings: the candidate budget, and the candidate judge and its
        model for layer 4b.
        """
        judge = self.model_for("CANDIDATE_JUDGE")
        return {
            "candidates": self.router_candidates,
            "llm_candidate_judge": self.router_candidate_judge,
            "judge_workers": self.router_judge_workers,
            "no_judge_batch": not self.router_judge_batch,
            "provider": judge.provider,
            "model": judge.model,
            "temperature": judge.temperature,
        }

    # -- identity -----------------------------------------------------------

    def model_for(self, module: str) -> LLMSettings:
        """The settings for one module, falling back to its own configuration."""
        return self.models.get(module) or llm_settings(module)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-friendly view, for hashing and for recording what a run used."""
        return {
            "models": {
                module: vars(settings) for module, settings in sorted(self.models.items())
            },
            "topology": self.topology,
            "player_tool_mode": self.player_tool_mode,
            "player_tool_iterations": self.player_tool_iterations,
            "catalog_prose_tier": self.catalog_prose_tier,
            "catalog_debug": self.catalog_debug,
            "router_candidates": self.router_candidates,
            "router_candidate_judge": self.router_candidate_judge,
            "router_judge_workers": self.router_judge_workers,
            "router_judge_batch": self.router_judge_batch,
            "thresholds": self.thresholds.to_dict(),
        }

    def token(self) -> str:
        """A short stable digest of these settings.

        Two things key off it: a cached result, which belongs to the settings that
        produced it, and the widgets whose defaults these settings are, which must
        be new widgets once the answer behind them changes.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.blake2s(payload.encode(), digest_size=5).hexdigest()

    def summary(self) -> str:
        """One line naming what is configured, for a collapsed panel's header."""
        llm_stages = []
        if self.catalog_prose_tier != "off":
            llm_stages.append(f"catalog prose: {self.catalog_prose_tier}")
        if self.router_candidate_judge:
            llm_stages.append("candidate judge: on")
        parts = [
            f"planning {self.model_for('PLANNING').model}",
            f"topology {self.topology}",
            *llm_stages,
        ]
        return " · ".join(parts)


def defaults() -> PipelineSettings:
    """The settings the environment configures, before anyone touches the panel."""
    return PipelineSettings(
        models={module: llm_settings(module) for module in LLM_MODULES},
        topology=DEFAULT_TOPOLOGY,
        player_tool_mode=player_tool_execution_mode(),
        player_tool_iterations=player_max_tool_iterations(),
        thresholds=Thresholds.from_environment(),
    )


def current() -> PipelineSettings:
    """What the app is configured to do, as last set on the landing page.

    A page reached before the landing page has rendered — a deep link, a fresh
    session — gets the environment's own answer rather than nothing.
    """
    settings = st.session_state.get(_SESSION_KEY)
    return settings if isinstance(settings, PipelineSettings) else defaults()


def render() -> PipelineSettings:
    """Render the settings panel, publish the result, and return it.

    One tab per module, named as the module is named elsewhere in the app, holding
    everything that module takes — its own parameters and the model it calls — plus
    an **Overview** tab gathering every module's settings on one page. The overview
    and the module tabs are two views of the same values: a change made in either is
    what the other shows (see :class:`_View`).

    Returns:
        The settings every stage should run with.
    """
    settings = current()
    with st.expander(f"⚙️ Pipeline settings — {settings.summary()}", expanded=False):
        overview_tab, *module_tabs = st.tabs(["Overview", *(title for title, _ in _MODULES)])
        with overview_tab:
            st.caption(
                "Every module's settings on one page. The tabs beside this one show "
                "the same settings a module at a time; a change in either place is a "
                "change in both."
            )
            chosen = {}
            for title, render_module in _MODULES:
                with st.container(border=True):
                    st.markdown(f"#### {title}")
                    chosen[title] = render_module(_View("overview"))
        for (title, render_module), tab in zip(_MODULES, module_tabs):
            with tab:
                render_module(_View("module"))

        if st.button("Reset to the configured defaults", key=f"{_WIDGET_PREFIX}.reset"):
            _clear_widgets()
            st.rerun()

    planning_model = chosen["Planning"]
    topology, tool_mode, tool_iterations, player_model = chosen["Players"]
    prose_tier, catalog_debug, catalog_model, catalog_limits = chosen["Catalog resolver"]
    candidates, candidate_judge, workers, batch, judge_model, router_limits = chosen[
        "Field router"
    ]
    settings = PipelineSettings(
        # A module added to LLM_MODULES without a place here still runs, on its
        # configured model; it just is not adjustable until it is given one.
        models={
            **{module: llm_settings(module) for module in LLM_MODULES},
            "PLANNING": planning_model,
            "PLAYER": player_model,
            "CATALOG_RESOLVER": catalog_model,
            "CANDIDATE_JUDGE": judge_model,
        },
        topology=topology,
        player_tool_mode=tool_mode,
        player_tool_iterations=tool_iterations,
        catalog_prose_tier=prose_tier,
        catalog_debug=catalog_debug,
        router_candidates=candidates,
        router_candidate_judge=candidate_judge,
        router_judge_workers=workers,
        router_judge_batch=batch,
        thresholds=Thresholds(**catalog_limits, **router_limits),
    )
    st.session_state[_SESSION_KEY] = settings
    return settings


class _View:
    """One place the panel draws its controls: the overview, or a module's own tab.

    Streamlit ties a widget's value to its key, and no two widgets may share one, so a
    setting shown in two places is two widgets. Each setting therefore keeps one
    *stored* value, and every widget showing it is a view of that value: before a
    widget is drawn its state is set from the stored value, and when it is changed it
    writes back. The next rerun draws every view from the new value, so the overview
    and the module tab never disagree.

    Widgets are given no ``value=``/``index=`` of their own — their state comes from
    the stored value alone, which is what keeps Streamlit from warning that a widget
    has a default and a session-state value at once.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def bind(self, section: str, setting: str, default: Any) -> dict[str, Any]:
        """Widget arguments that tie this view's widget to the stored value."""
        stored = _key(section, setting)
        widget = f"{stored}@{self.name}"
        if stored not in st.session_state:
            st.session_state[stored] = default
        st.session_state[widget] = st.session_state[stored]
        return {"key": widget, "on_change": _store, "args": (widget, stored)}


def _store(widget: str, stored: str) -> None:
    """Write a changed widget's value back to the setting it shows."""
    st.session_state[stored] = st.session_state[widget]


def _applies_to(where: str) -> None:
    """Say which part of the app a module's settings reach."""
    st.caption(f"Applies to: {where}")


def _render_model(
    view: _View, module: str, *, disabled: bool = False, off_note: str = ""
) -> LLMSettings:
    """The model one module calls: provider, model name, and temperature.

    ``disabled`` greys the row out while the module is not calling a model; the
    values are kept, so turning the module back on restores them.
    """
    spec = LLM_MODULES[module]
    configured = llm_settings(module)
    providers = list(PROVIDER_CONFIGS)
    st.markdown(f"**{spec.label} model**")
    st.caption(
        f"{spec.description} Same as `LLM_MODEL_{module}` (and its provider and "
        "temperature) in `.env`; left alone, it follows `LLM_PROVIDER` / `LLM_MODEL`."
        + (f" {off_note}" if disabled and off_note else "")
    )
    provider_column, model_column, temperature_column = st.columns(
        [1, 2, 1], gap="medium"
    )
    with provider_column:
        provider = st.selectbox(
            "Provider",
            options=providers,
            help=PROVIDER_CONFIGS.get(configured.provider, {}).get("description"),
            disabled=disabled,
            **view.bind(module, "provider", providers[_index_of(providers, configured.provider)]),
        )
    with model_column:
        model = st.text_input(
            "Model",
            help=(
                "Model name as the provider spells it. Its default is "
                f"{PROVIDER_CONFIGS.get(provider, {}).get('default_model', '—')}."
            ),
            disabled=disabled,
            **view.bind(module, "model", configured.model),
        ).strip()
    with temperature_column:
        temperature = st.slider(
            "Temperature",
            min_value=0.0,
            max_value=2.0,
            step=0.05,
            help=f"This module wants {spec.temperature} by default.",
            disabled=disabled,
            **view.bind(module, "temperature", float(configured.temperature)),
        )
    return LLMSettings(
        provider=provider,
        model=model or configured.model,
        temperature=float(temperature),
    )


def _render_planning(view: _View) -> LLMSettings:
    """The planner: the model that writes the execution plan."""
    _applies_to("**Metadata generation** — the plan written before any step runs.")
    return _render_model(view, "PLANNING")


def _render_players(view: _View) -> tuple[str, str, int, LLMSettings]:
    """The players: how the plan is executed, how freely they use tools, their model."""
    _applies_to("**Metadata generation** — every step of the plan.")
    topologies = list(EXECUTION_TOPOLOGIES)
    modes = list(PLAYER_TOOL_MODES)
    topology_column, mode_column, iterations_column = st.columns(3, gap="medium")
    with topology_column:
        topology = st.selectbox(
            "Topology",
            options=topologies,
            help="How many players work each step, and how many debate rounds they run.",
            **view.bind("execution", "topology", topologies[_index_of(topologies, DEFAULT_TOPOLOGY)]),
        )
        st.caption(EXECUTION_TOPOLOGIES[topology].get("description", ""))
    with mode_column:
        mode = st.selectbox(
            "Player tool use",
            options=modes,
            help=(
                "`investigate` lets a player call the tools whose arguments only the "
                "model can supply; `survey` runs the deterministic survey alone."
            ),
            **view.bind("execution", "tool_mode", modes[_index_of(modes, player_tool_execution_mode())]),
        )
    with iterations_column:
        iterations = st.number_input(
            "Max tool rounds",
            min_value=1,
            max_value=32,
            step=1,
            help="Model↔tool rounds a player may take per task while investigating.",
            **view.bind("execution", "tool_iterations", int(player_max_tool_iterations())),
        )
    return topology, mode, int(iterations), _render_model(view, "PLAYER")


def _render_thresholds(view: _View, *stages: str) -> dict[str, Any]:
    """The thresholds of ``stages``, as number inputs; returns their values by name.

    Each starts from what the environment configures (``THRESHOLD_<NAME>`` in ``.env``,
    else the default in :mod:`src.thresholds`), and a value set here reaches every run
    the app starts.
    """
    configured = Thresholds.from_environment()
    values: dict[str, Any] = {}
    for stage in stages:
        specs = threshold_registry.specs(stage)
        if not specs:
            continue
        st.markdown(f"**{stage} thresholds**")
        st.caption(
            "Same as `THRESHOLD_<NAME>` in `.env`. Hover a control for what it decides."
        )
        columns = st.columns(2, gap="medium")
        for index, spec in enumerate(specs):
            default = getattr(configured, spec.name)
            is_int = isinstance(spec.default, int)
            with columns[index % 2]:
                value = st.number_input(
                    spec.metadata["label"],
                    min_value=spec.metadata["min"] if is_int else float(spec.metadata["min"]),
                    step=1 if is_int else 0.05,
                    format=None if is_int else "%.2f",
                    help=f"{spec.metadata['help']} (`{env_name(spec.name)}`)",
                    **view.bind("thresholds", spec.name, default),
                )
            values[spec.name] = int(value) if is_int else float(value)
    return values


def _render_catalog(view: _View) -> tuple[str, bool, LLMSettings, dict[str, Any]]:
    """Layer 3: how hard the resolver works to find what a column means, and its model."""
    _applies_to(
        "the **Catalog resolver** module's starting values — and so the catalog the "
        "Field router routes."
    )
    st.caption(
        "Columns are resolved from the bundle's codebooks first, then from its prose. "
        "The tier chosen here is the highest one that runs."
    )
    tiers = list(PROSE_TIERS)
    tier_column, debug_column = st.columns([2, 1], gap="medium")
    with tier_column:
        tier = st.radio(
            "Prose tier",
            options=tiers,
            horizontal=True,
            format_func=str.capitalize,
            **view.bind("catalog", "prose_tier", "off"),
        )
        st.caption(PROSE_TIERS[tier])
    with debug_column:
        debug = st.checkbox(
            "Log prompts and responses",
            help=(
                "Show every prompt and raw response the prose reader exchanges, and "
                "surface errors it would otherwise swallow into a silent abstention."
            ),
            disabled=tier != "llm",
            **view.bind("catalog", "debug", False),
        )
    model = _render_model(
        view, "CATALOG_RESOLVER", disabled=tier != "llm",
        off_note="Used only with the `llm` prose tier.",
    )
    limits = _render_thresholds(view, threshold_registry.CATALOG)
    return tier, bool(debug), model, limits


def _render_router(
    view: _View,
) -> tuple[int, bool, int, bool, LLMSettings, dict[str, Any]]:
    """Layer 4: how many sources a field keeps, who judges them, and the judge's model."""
    _applies_to("the **Field router** module's starting values.")
    st.caption(
        "Ranking alone over-answers — BM25's only reject rule is a non-empty score — "
        "so the judge exists to say that *none* of the candidates answers a field."
    )
    candidates_column, judge_column = st.columns([1, 2], gap="medium")
    with candidates_column:
        candidates = st.number_input(
            "Candidates per field",
            min_value=1,
            max_value=20,
            step=1,
            help=(
                "The recall budget, not a display setting: the router proposes this "
                "many and the executor picks from them."
            ),
            **view.bind("routing", "candidates", 5),
        )
    with judge_column:
        candidate_judge = st.checkbox(
            "LLM candidate judge",
            help=(
                "Let the model decide which candidate answers each field, or that "
                "none does. Without it rank 1 wins on lexical score alone."
            ),
            **view.bind("routing", "candidate_judge", False),
        )
        workers_column, batch_column = st.columns(2, gap="medium")
        with workers_column:
            workers = st.number_input(
                "Concurrent calls",
                min_value=1,
                max_value=32,
                step=1,
                disabled=not candidate_judge,
                help=(
                    "A self-hosted endpoint batches concurrent requests internally, "
                    "so this is usually the largest win on a slow model."
                ),
                **view.bind("routing", "judge_workers", 1),
            )
        with batch_column:
            batch = st.checkbox(
                "Ask about many fields per call",
                disabled=not candidate_judge,
                help=(
                    "Match many fields against the catalog, and read a passage for many "
                    "fields, in one call. Turning it off judges every field "
                    "independently, and costs a call each."
                ),
                **view.bind("routing", "judge_batch", True),
            )
    model = _render_model(
        view, "CANDIDATE_JUDGE", disabled=not candidate_judge,
        off_note="Used only with the LLM candidate judge on.",
    )
    limits = _render_thresholds(view, threshold_registry.ROUTER, threshold_registry.COMPILER)
    return int(candidates), bool(candidate_judge), int(workers), bool(batch), model, limits


#: The modules the panel gives a tab, in tab order, with what renders each.
_MODULES = (
    ("Planning", _render_planning),
    ("Players", _render_players),
    ("Catalog resolver", _render_catalog),
    ("Field router", _render_router),
)


def _key(section: str, name: str) -> str:
    """The session-state key one setting keeps its stored value under."""
    return f"{_WIDGET_PREFIX}.{section}.{name}".lower()


def _index_of(options: list[str], value: str) -> int:
    """Where ``value`` sits in ``options``, or the first entry when it is absent."""
    return options.index(value) if value in options else 0


def _clear_widgets() -> None:
    """Forget every setting and widget value, so each falls back to its configured default."""
    for key in [k for k in st.session_state if k.startswith(f"{_WIDGET_PREFIX}.")]:
        del st.session_state[key]
