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
from src.topology import EXECUTION_TOPOLOGIES


#: The prose tiers layer 3 can run above the codebook lookup and the value prior,
#: in increasing order of what they can read — and of what they cost.
PROSE_TIERS = {
    "off": "Structured codebooks and the value prior only. No prose is read.",
    "deterministic": "Retrieve-then-read: finds a *cued* definition. No model call.",
    "llm": "Reads free narrative with the catalog reader's model.",
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
        router_field_reader: Let a model adjudicate the candidates (layer 4b).
        router_reader_workers: How many of the reader's calls to issue at once.
        router_reader_batch: Ask once about fields offered identical candidates,
            rather than once per field.
    """

    models: Mapping[str, LLMSettings] = field(default_factory=dict)
    topology: str = DEFAULT_TOPOLOGY
    player_tool_mode: str = "investigate"
    player_tool_iterations: int = 8
    catalog_prose_tier: str = "off"
    catalog_debug: bool = False
    router_candidates: int = 5
    router_field_reader: bool = False
    router_reader_workers: int = 1
    router_reader_batch: bool = True

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
        return env

    def catalog_arguments(self) -> dict[str, Any]:
        """Starting values for ``examples/resolve_catalog.py``'s form, by ``dest``."""
        model = self.model_for("CATALOG_RESOLVER")
        return {
            "prose_reader": self.catalog_prose_tier == "deterministic",
            "llm_reader": self.catalog_prose_tier == "llm",
            # There are no prompts to log unless a model is the one reading.
            "debug": self.catalog_debug and self.catalog_prose_tier == "llm",
            "provider": model.provider,
            "model": model.model,
            "temperature": model.temperature,
        }

    def router_arguments(self) -> dict[str, Any]:
        """Starting values for ``examples/field_router_plan.py``'s form, by ``dest``.

        The router resolves a catalog before it routes, so it takes both stages'
        settings: the prose tier and its model for layer 3, the field reader and
        its model for layer 4b.
        """
        catalog = self.model_for("CATALOG_RESOLVER")
        reader = self.model_for("FIELD_READER")
        return {
            "prose_reader": self.catalog_prose_tier == "deterministic",
            "llm_reader": self.catalog_prose_tier == "llm",
            "catalog_provider": catalog.provider,
            "catalog_model": catalog.model,
            "catalog_temperature": catalog.temperature,
            "candidates": self.router_candidates,
            "field_reader": self.router_field_reader,
            "reader_workers": self.router_reader_workers,
            "no_reader_batch": not self.router_reader_batch,
            "provider": reader.provider,
            "model": reader.model,
            "temperature": reader.temperature,
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
            "router_field_reader": self.router_field_reader,
            "router_reader_workers": self.router_reader_workers,
            "router_reader_batch": self.router_reader_batch,
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
        readers = []
        if self.catalog_prose_tier != "off":
            readers.append(f"catalog prose: {self.catalog_prose_tier}")
        if self.router_field_reader:
            readers.append("field reader: on")
        parts = [
            f"planning {self.model_for('PLANNING').model}",
            f"topology {self.topology}",
            *readers,
        ]
        return " · ".join(parts)


def defaults() -> PipelineSettings:
    """The settings the environment configures, before anyone touches the panel."""
    return PipelineSettings(
        models={module: llm_settings(module) for module in LLM_MODULES},
        topology=DEFAULT_TOPOLOGY,
        player_tool_mode=player_tool_execution_mode(),
        player_tool_iterations=player_max_tool_iterations(),
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

    Returns:
        The settings every stage should run with.
    """
    settings = current()
    with st.expander(f"⚙️ Pipeline settings — {settings.summary()}", expanded=False):
        model_tab, execution_tab, catalog_tab, routing_tab = st.tabs(
            ["Models", "Planning & execution", "Catalog resolution", "Field routing"]
        )
        with model_tab:
            models = _render_models()
        with execution_tab:
            topology, tool_mode, tool_iterations = _render_execution()
        with catalog_tab:
            prose_tier, catalog_debug = _render_catalog()
        with routing_tab:
            candidates, field_reader, workers, batch = _render_routing()

        if st.button("Reset to the configured defaults", key=f"{_WIDGET_PREFIX}.reset"):
            _clear_widgets()
            st.rerun()

    settings = PipelineSettings(
        models=models,
        topology=topology,
        player_tool_mode=tool_mode,
        player_tool_iterations=tool_iterations,
        catalog_prose_tier=prose_tier,
        catalog_debug=catalog_debug,
        router_candidates=candidates,
        router_field_reader=field_reader,
        router_reader_workers=workers,
        router_reader_batch=batch,
    )
    st.session_state[_SESSION_KEY] = settings
    return settings


def _render_models() -> dict[str, LLMSettings]:
    """One row per module that calls a model: who, which model, how hot."""
    st.caption(
        "Each stage picks its own model. A stage left alone follows "
        "`LLM_PROVIDER` / `LLM_MODEL`; naming one here is the same override as "
        "setting `LLM_MODEL_<STAGE>` in `.env`."
    )
    models: dict[str, LLMSettings] = {}
    for module, spec in LLM_MODULES.items():
        configured = llm_settings(module)
        st.markdown(f"**{spec.label}**")
        st.caption(spec.description)
        provider_column, model_column, temperature_column = st.columns(
            [1, 2, 1], gap="medium"
        )
        with provider_column:
            provider = st.selectbox(
                "Provider",
                options=list(PROVIDER_CONFIGS),
                index=_index_of(list(PROVIDER_CONFIGS), configured.provider),
                key=_key(module, "provider"),
                help=PROVIDER_CONFIGS.get(configured.provider, {}).get("description"),
            )
        with model_column:
            model = st.text_input(
                "Model",
                value=configured.model,
                key=_key(module, "model"),
                help=(
                    "Model name as the provider spells it. Its default is "
                    f"{PROVIDER_CONFIGS.get(provider, {}).get('default_model', '—')}."
                ),
            ).strip()
        with temperature_column:
            temperature = st.slider(
                "Temperature",
                min_value=0.0,
                max_value=2.0,
                value=float(configured.temperature),
                step=0.05,
                key=_key(module, "temperature"),
                help=f"This stage wants {spec.temperature} by default.",
            )
        models[module] = LLMSettings(
            provider=provider,
            model=model or configured.model,
            temperature=float(temperature),
        )
    return models


def _render_execution() -> tuple[str, str, int]:
    """How the generated plan is executed, and how freely players may use tools."""
    topology_column, mode_column, iterations_column = st.columns(3, gap="medium")
    with topology_column:
        topologies = list(EXECUTION_TOPOLOGIES)
        topology = st.selectbox(
            "Topology",
            options=topologies,
            index=_index_of(topologies, DEFAULT_TOPOLOGY),
            key=_key("execution", "topology"),
            help="How many players work each step, and how many debate rounds they run.",
        )
        st.caption(EXECUTION_TOPOLOGIES[topology].get("description", ""))
    with mode_column:
        mode = st.selectbox(
            "Player tool use",
            options=list(PLAYER_TOOL_MODES),
            index=_index_of(list(PLAYER_TOOL_MODES), player_tool_execution_mode()),
            key=_key("execution", "tool_mode"),
            help=(
                "`investigate` lets a player call the tools whose arguments only the "
                "model can supply; `survey` runs the deterministic survey alone."
            ),
        )
    with iterations_column:
        iterations = st.number_input(
            "Max tool rounds",
            min_value=1,
            max_value=32,
            value=player_max_tool_iterations(),
            step=1,
            key=_key("execution", "tool_iterations"),
            help="Model↔tool rounds a player may take per task while investigating.",
        )
    return topology, mode, int(iterations)


def _render_catalog() -> tuple[str, bool]:
    """Layer 3: how hard the resolver works to find what a column means."""
    st.caption(
        "Columns are resolved from the bundle's codebooks first, then from its prose. "
        "The tier chosen here is the highest one that runs."
    )
    tier_column, debug_column = st.columns([2, 1], gap="medium")
    with tier_column:
        tiers = list(PROSE_TIERS)
        tier = st.radio(
            "Prose tier",
            options=tiers,
            index=_index_of(tiers, "off"),
            key=_key("catalog", "prose_tier"),
            horizontal=True,
            format_func=str.capitalize,
        )
        st.caption(PROSE_TIERS[tier])
    with debug_column:
        debug = st.checkbox(
            "Log prompts and responses",
            value=False,
            key=_key("catalog", "debug"),
            help=(
                "Show every prompt and raw response the prose reader exchanges, and "
                "surface errors it would otherwise swallow into a silent abstention."
            ),
            disabled=tier != "llm",
        )
    return tier, bool(debug)


def _render_routing() -> tuple[int, bool, int, bool]:
    """Layer 4: how many sources a field keeps, and who adjudicates them."""
    st.caption(
        "Ranking alone over-answers — BM25's only reject rule is a non-empty score — "
        "so the reader exists to say that *none* of the candidates answers a field."
    )
    candidates_column, reader_column = st.columns([1, 2], gap="medium")
    with candidates_column:
        candidates = st.number_input(
            "Candidates per field",
            min_value=1,
            max_value=20,
            value=5,
            step=1,
            key=_key("routing", "candidates"),
            help=(
                "The recall budget, not a display setting: the router proposes this "
                "many and the executor picks from them."
            ),
        )
    with reader_column:
        field_reader = st.checkbox(
            "Adjudicate candidates with the field reader",
            value=False,
            key=_key("routing", "field_reader"),
            help=(
                "Let the model decide which candidate answers each field, or that "
                "none does. Without it rank 1 wins on lexical score alone."
            ),
        )
        workers_column, batch_column = st.columns(2, gap="medium")
        with workers_column:
            workers = st.number_input(
                "Concurrent calls",
                min_value=1,
                max_value=32,
                value=1,
                step=1,
                key=_key("routing", "reader_workers"),
                disabled=not field_reader,
                help=(
                    "A self-hosted endpoint batches concurrent requests internally, "
                    "so this is usually the largest win on a slow model."
                ),
            )
        with batch_column:
            batch = st.checkbox(
                "Group identical candidate sets",
                value=True,
                key=_key("routing", "reader_batch"),
                disabled=not field_reader,
                help=(
                    "Ask once about fields offered the same candidates. Turning it "
                    "off judges every field independently, and costs a call each."
                ),
            )
    return int(candidates), bool(field_reader), int(workers), bool(batch)


def _key(section: str, name: str) -> str:
    """The session-state key one control keeps its value under."""
    return f"{_WIDGET_PREFIX}.{section}.{name}".lower()


def _index_of(options: list[str], value: str) -> int:
    """Where ``value`` sits in ``options``, or the first entry when it is absent."""
    return options.index(value) if value in options else 0


def _clear_widgets() -> None:
    """Forget every control's value, so each falls back to its configured default."""
    for key in [k for k in st.session_state if k.startswith(f"{_WIDGET_PREFIX}.")]:
        del st.session_state[key]
