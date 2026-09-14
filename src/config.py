"""
Centralized configuration for the Metadata Agent.

This module loads ``.env`` values to configure provider selection, model behavior, and
execution defaults. Define values in a local ``.env`` file for development or set
them directly in the shell/hosting environment for deployments.

Configuration Options
---------------------

``LLM_PROVIDER``
    Selects the backend provider. Supported values are ``"google"``,
    ``"surf"``, and ``"openai"``. Defaults to ``"google"``.
``LLM_MODEL``
    Optional explicit model name. If unset, the selected provider's default
    model from :data:`PROVIDER_CONFIGS` is used.
``LLM_<FIELD>_<MODULE>``
    Per-module overrides, for every module named in :data:`LLM_MODULES` and every
    field of :class:`LLMSettings` — ``LLM_MODEL_PLANNING``,
    ``LLM_TEMPERATURE_CATALOG_RESOLVER``, ``LLM_PROVIDER_CANDIDATE_JUDGE``, and so on.
    A module with none set follows the global settings above.
``PLAYER_TOOL_EXECUTION_MODE``
    ``"investigate"`` (default) or ``"survey"``; see :data:`PLAYER_TOOL_MODES`.
``PLAYER_MAX_TOOL_ITERATIONS``
    Max model↔tool rounds per task while investigating. Defaults to ``8``.
``DEFAULT_TOPOLOGY``
    Default execution topology name. Defaults to ``"default"``.
``DEFAULT_METADATA_STANDARD``
    Default metadata standard. Defaults to ``"basic"``.

Provider Credentials and Endpoints
----------------------------------

``google``
    Requires ``GOOGLE_API_KEY``. Default model is ``"gemini-2.5-flash"``.
``surf``
    Requires ``SURF_API_BASE`` and ``SURF_API_KEY`` for an OpenAI-compatible
    custom endpoint such as vLLM or TGI. Default model is
    ``"Qwen 2.5 Coder 32B Instruct AWQ"``.
``openai``
    Requires ``OPENAI_API_KEY``. Default model is ``"gpt-4o-mini"``.

Runtime Overrides
-----------------

* Pass ``provider`` to :func:`create_llm` to override ``LLM_PROVIDER`` for one
  call.
* Pass ``model_name`` to :func:`create_llm` or :func:`get_model_name` to
  override ``LLM_MODEL`` for one call.
* Pass ``temperature`` to :func:`create_llm` to override the module's default
  temperature for one model instance.

Every value here is read from the environment when it is asked for, so setting a
variable at runtime — a UI offering a model picker, a test fixing a temperature —
takes effect on the next call rather than needing a fresh process.

Example ``.env`` file::

    LLM_PROVIDER=surf
    SURF_API_KEY=...
    SURF_API_BASE={SURF_ENDPOINT_URL}
    LLM_MODEL=Qwen 2.5 Coder 32B Instruct AWQ
    LLM_TEMPERATURE_PLANNING=0.0
    LLM_TEMPERATURE_PLAYER=0.3
    DEFAULT_TOPOLOGY=default
"""
import os
from dataclasses import dataclass
from typing import Optional, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# =============================================================================
# LLM PROVIDER CONFIGURATION
# =============================================================================

# Every environment-backed value below is read when it is *asked for*, not when
# this module is imported. A front end that lets someone choose a model can then
# set the variable and have the next call honour it; a constant captured at import
# would freeze whatever the process started with.

def default_provider() -> str:
    """The provider a module falls back to: ``LLM_PROVIDER``, else Google."""
    return os.getenv("LLM_PROVIDER", "google")


# Provider-specific configurations
PROVIDER_CONFIGS = {
    "google": {
        "default_model": "gemini-2.5-flash",
        "api_key_env": "GOOGLE_API_KEY",
        "description": "Google Gemini models",
    },
    "surf": {
        "default_model": "Qwen 2.5 Coder 32B Instruct AWQ",
        "api_key_env": "SURF_API_KEY",
        "base_url_env": "SURF_API_BASE",
        "description": "Custom OpenAI-compatible endpoint (e.g., vLLM, TGI)",
    },
    "openai": {
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "description": "OpenAI models",
    },
}

# =============================================================================
# LLM MODEL CONFIGURATION
# =============================================================================

def default_model() -> Optional[str]:
    """``LLM_MODEL``, or None to fall back to the provider's own default model."""
    return os.getenv("LLM_MODEL") or None


# =============================================================================
# PER-MODULE LLM SELECTION
# =============================================================================
#
# The modules that call a model want different ones: planning wants a strong
# reasoner, extraction can be cheaper, the catalog resolver's prose reader only
# has to copy out definitions a document already states. Each therefore resolves
# its own provider, model, and temperature, in this order:
#
#   1. an explicit argument (a --provider / --model / --temperature flag)
#   2. the module's own environment variable, LLM_<FIELD>_<MODULE>
#   3. the global LLM_PROVIDER / LLM_MODEL / the module's default temperature
#
# So an unconfigured module behaves exactly as it did before this existed, and
# pointing one module at another model is one line in .env.

@dataclass(frozen=True)
class LLMModule:
    """One model-selecting module: what it is, and the temperature it wants.

    The label and the blurb are here rather than in the UI that shows them because
    they describe the module, not a rendering of it: ``--help``, a settings panel,
    and a configuration summary all want the same two sentences.
    """

    label: str
    temperature: float
    description: str


#: Modules that select a model, keyed by the name their env vars are spelled with.
LLM_MODULES = {
    "PLANNING": LLMModule(
        "Planning", 0.0,
        "Writes the execution plan. Deterministic, and wants the strongest reasoner.",
    ),
    "PLAYER": LLMModule(
        "Player", 0.3,
        "Extract and synthesize metadata for each plan step. A little latitude helps.",
    ),
    "CATALOG_RESOLVER": LLMModule(
        "LLM prose reader", 0.0,
        "Reads a column's meaning out of free narrative. Copies stated definitions, "
        "so it can be a cheaper model.",
    ),
    "CANDIDATE_JUDGE": LLMModule(
        "LLM candidate judge", 0.0,
        "Decides which routed candidate answers a schema field, or that none does. "
        "A judgement, not a draft.",
    ),
}


@dataclass(frozen=True)
class LLMSettings:
    """The provider, model, and temperature one module should run with."""

    provider: str
    model: str
    temperature: float

    def describe(self) -> str:
        """A short label naming the model, for output that records what ran."""
        return f"{self.provider}:{self.model} @ T={self.temperature}"


def module_default_temperature(module: Optional[str]) -> float:
    """The temperature ``module`` declares in :data:`LLM_MODULES`, else 0.0."""
    spec = LLM_MODULES.get((module or "").upper())
    return spec.temperature if spec else 0.0


def _module_env(field: str, module: Optional[str]) -> Optional[str]:
    """Read ``LLM_<FIELD>_<MODULE>``, or None when unset or module-less."""
    if not module:
        return None
    return os.getenv(f"LLM_{field}_{module.upper().replace('-', '_')}")


def llm_settings(
    module: Optional[str] = None,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> LLMSettings:
    """Resolve which model ``module`` should use.

    :param module: Module name, e.g. ``"PLANNING"``. Omit for the global default.
    :param provider: Explicit provider, overriding any configuration.
    :param model: Explicit model name, overriding any configuration.
    :param temperature: Explicit temperature, overriding any configuration.
    :returns: The resolved :class:`LLMSettings`.

    A module that names a provider but no model gets *that provider's* default
    model rather than the global ``LLM_MODEL``, which belongs to whichever
    provider ``LLM_PROVIDER`` names.
    """
    global_provider = default_provider()
    resolved_provider = provider or _module_env("PROVIDER", module) or global_provider

    resolved_model = model or _module_env("MODEL", module)
    if not resolved_model:
        # LLM_MODEL is stated for the global provider; it does not transfer to a
        # module that switched providers.
        global_model = default_model()
        if resolved_provider == global_provider and global_model:
            resolved_model = global_model
        else:
            resolved_model = PROVIDER_CONFIGS.get(resolved_provider, {}).get(
                "default_model", "gpt-4o-mini"
            )

    if temperature is None:
        from_env = _module_env("TEMPERATURE", module)
        temperature = (
            float(from_env)
            if from_env is not None
            else module_default_temperature(module)
        )

    return LLMSettings(
        provider=resolved_provider, model=resolved_model, temperature=temperature
    )


def create_llm_for(module: Optional[str] = None, **overrides: Any) -> Any:
    """Build the chat model ``module`` is configured to use.

    :param module: Module name, as in :func:`llm_settings`.
    :param overrides: ``provider`` / ``model`` / ``temperature`` overrides, plus
        any additional keyword arguments for the model constructor.
    :returns: LangChain chat model instance.
    """
    settings = llm_settings(
        module,
        provider=overrides.pop("provider", None),
        model=overrides.pop("model", None),
        temperature=overrides.pop("temperature", None),
    )
    return create_llm(
        model_name=settings.model,
        temperature=settings.temperature,
        provider=settings.provider,
        **overrides,
    )


#: How a player may use the tools it was given.
#:
#:   ``"investigate"`` — after the deterministic survey, let the model call the
#:   tools whose arguments only it can supply (column names, and so on).
#:   ``"survey"`` — survey only; tools needing model-chosen arguments never run.
PLAYER_TOOL_MODES = ("investigate", "survey")


def player_tool_execution_mode() -> str:
    """``PLAYER_TOOL_EXECUTION_MODE``, one of :data:`PLAYER_TOOL_MODES`."""
    return os.getenv("PLAYER_TOOL_EXECUTION_MODE", "investigate")


def player_max_tool_iterations() -> int:
    """Max model↔tool rounds per task during the investigation phase."""
    return int(os.getenv("PLAYER_MAX_TOOL_ITERATIONS", "8"))


# =============================================================================
# PROVIDER-SPECIFIC API KEYS AND ENDPOINTS
# =============================================================================

# Google
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Surf (custom OpenAI-compatible endpoint)
SURF_API_BASE = os.getenv("SURF_API_BASE")  # e.g., "http://localhost:8000/v1"
SURF_API_KEY = os.getenv("SURF_API_KEY")  # Required for Surf provider
SURF_ENABLE_THINKING = os.getenv("SURF_ENABLE_THINKING", "false").lower() == "true"

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# =============================================================================
# EXECUTION DEFAULTS
# =============================================================================

# Default execution topology
# Can be overridden by environment variable: DEFAULT_TOPOLOGY
DEFAULT_TOPOLOGY = os.getenv("DEFAULT_TOPOLOGY", "single")

# Default metadata standard
# Can be overridden by environment variable: DEFAULT_METADATA_STANDARD
DEFAULT_METADATA_STANDARD = os.getenv("DEFAULT_METADATA_STANDARD", "basic")

# TUI logging level (e.g., DEBUG, INFO, WARNING, ERROR)
# Can be overridden by environment variable: TUI_LOG_LEVEL
TUI_LOG_LEVEL = os.getenv("TUI_LOG_LEVEL", "INFO").upper()

# Comma-separated logger prefixes to suppress from TUI output
# Example: "src.orchestrator.step_executor,src.players.player"
TUI_LOG_SUPPRESSED_LOGGERS = os.getenv(
    "TUI_LOG_SUPPRESSED_LOGGERS",
    "src.orchestrator.step_executor,src.players.player",
)

# TUI user-facing log verbosity:
# - "quiet": status line only (no streamed log lines in chat)
# - "normal": status line + explicit UI messages
# - "debug": status line + all streamed log lines
TUI_UI_VERBOSITY = os.getenv("TUI_UI_VERBOSITY", "normal").lower()


# =============================================================================
# LLM FACTORY
# =============================================================================

def get_model_name(override: Optional[str] = None) -> str:
    """
    Get the model name to use.

    Priority:

    #. Override parameter.
    #. ``LLM_MODEL`` environment variable.
    #. Provider's default model.

    :param override: Optional model name to use instead of configured defaults.
    :returns: The resolved model name.
    """
    if override:
        return override
    configured = default_model()
    if configured:
        return configured
    return PROVIDER_CONFIGS.get(default_provider(), {}).get(
        "default_model", "gpt-4o-mini"
    )


def create_llm(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    provider: Optional[str] = None,
    **kwargs
) -> Any:
    """
    Create an LLM instance based on the configured provider.

    :param model_name: Model name. Uses the configured default if omitted.
    :param temperature: LLM temperature.
    :param provider: Provider to use instead of :data:`LLM_PROVIDER`.
    :param kwargs: Additional arguments passed to the LLM constructor.
    :returns: LangChain chat model instance.
    :raises ValueError: If the provider is unsupported or required
        configuration is missing.
    """
    provider = provider or default_provider()
    model = get_model_name(model_name)
    
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        if not GOOGLE_API_KEY:
            raise ValueError(
                "GOOGLE_API_KEY not found. Set it in your .env file."
            )
        
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=GOOGLE_API_KEY,
            **kwargs
        )
    
    elif provider == "surf":
        from langchain_openai import ChatOpenAI
        
        if not SURF_API_BASE:
            raise ValueError(
                "SURF_API_BASE not found. Set it in your .env file.\n"
                "Example: SURF_API_BASE=http://localhost:8000/v1"
            )
        
        if not SURF_API_KEY:
            raise ValueError(
                "SURF_API_KEY not found. Set it in your .env file."
            )
        
        model_kwargs = kwargs.pop("model_kwargs", {}) or {}
        extra_body = model_kwargs.pop("extra_body", {}) or {}
        chat_template_kwargs = extra_body.get("chat_template_kwargs", {}) or {}
        # Disable reasoning/thinking output by default for Surf-compatible backends.
        # This helps keep responses clean for downstream structured parsing.
        chat_template_kwargs.setdefault("enable_thinking", SURF_ENABLE_THINKING)
        extra_body["chat_template_kwargs"] = chat_template_kwargs

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            openai_api_key=SURF_API_KEY,
            openai_api_base=SURF_API_BASE,
            extra_body=extra_body,
            model_kwargs=model_kwargs,
            **kwargs
        )
    
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        
        if not OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY not found. Set it in your .env file."
            )
        
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            openai_api_key=OPENAI_API_KEY,
            **kwargs
        )
    
    else:
        available = list(PROVIDER_CONFIGS.keys())
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. Available: {available}"
        )


# =============================================================================
# CONFIGURATION SUMMARY
# =============================================================================

def get_config_summary() -> str:
    """Return a summary of current configuration."""
    llm_provider = default_provider()
    provider_config = PROVIDER_CONFIGS.get(llm_provider, {})
    model = get_model_name()
    
    # Check API key status
    api_key_env = provider_config.get("api_key_env", "")
    api_key_set = bool(os.getenv(api_key_env)) if api_key_env else False
    
    return f"""
        Agent Configuration:
        ----------------------
        LLM Provider: {llm_provider} ({provider_config.get('description', 'Unknown')})
        LLM Model: {model}
        Planning Temperature: {llm_settings("PLANNING").temperature}
        Player Temperature: {llm_settings("PLAYER").temperature}
        Default Topology: {DEFAULT_TOPOLOGY}
        Default Metadata Standard: {DEFAULT_METADATA_STANDARD}
        API Key ({api_key_env}): {'Set' if api_key_set else 'Not Set'}
        """
