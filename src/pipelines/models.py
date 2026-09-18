"""Build the model-backed roles a stage runs with — or none, and stay deterministic.

Every role in :mod:`src.llm_roles` takes one dependency: a callable ``prompt -> text``.
This module turns a module's configured provider, model and temperature
(:mod:`src.config`) into that callable, and groups the roles each stage needs:
:class:`Readers` for catalog resolution, :class:`Judges` for field routing.

Nothing here prints. A caller that wants to watch the traffic passes ``log``, a
``(kind, text) -> None`` callback receiving ``"prompt"``, ``"response"`` and ``"error"``
— which is how the CLI's ``--debug`` shows prompts without a console reaching into a
pipeline, and how an error is surfaced that a role would otherwise turn into a silent
abstention.

The model is built lazily, only when a stage actually asks for one, so the whole
deterministic path runs with no provider SDK installed and no credentials configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.config import LLMSettings, llm_settings
from src.router.catalog import (
    CachedProseReader,
    ClaimComparer,
    LLMClaimComparer,
    LLMProseReader,
    ProseReader,
)
from src.router.column_matcher import ColumnMatcher, LLMColumnMatcher
from src.router.passage_reader import LLMPassageReader, PassageReader
from src.router.tool_matcher import LLMToolMatcher, ToolMatcher

REPO = Path(__file__).resolve().parents[2]

#: Which :data:`~src.config.LLM_MODULES` entry each stage's roles draw their model from.
CATALOG_MODULE = "CATALOG_RESOLVER"
JUDGE_MODULE = "CANDIDATE_JUDGE"

#: Where the tool matcher keeps its answers. They depend only on the standard, the tool
#: set and the model, so they hold across bundles; delete the directory to ask again.
TOOL_MATCH_CACHE = REPO / ".cache" / "tool_matcher"

#: What a ``log`` callback is handed: the kind of text, and the text.
Log = Callable[[str, str], None]


@dataclass(frozen=True)
class Readers:
    """The roles catalog resolution (layer 3) may run with, and a label naming them."""

    prose: Optional[ProseReader] = None
    comparer: Optional[ClaimComparer] = None
    label: str = "off"


@dataclass(frozen=True)
class Judges:
    """The roles field routing (layer 4b) may run with, and a label naming them."""

    tools: Optional[ToolMatcher] = None
    columns: Optional[ColumnMatcher] = None
    passages: Optional[PassageReader] = None
    label: str = "off"


def invoker(
    module: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    log: Optional[Log] = None,
) -> tuple[Callable[[str], str], LLMSettings]:
    """A ``prompt -> text`` callable on ``module``'s model, and the settings behind it.

    ``log`` sees every prompt and reply, and an exception before it is re-raised: a role
    treats a failed call as an abstention, and those two are worth telling apart.
    """
    from src.config import create_llm_for      # lazy: pulls provider SDKs when used

    settings = llm_settings(
        module, provider=provider, model=model, temperature=temperature
    )
    chat = create_llm_for(module, **vars(settings))

    def invoke(prompt: str) -> str:
        if log is None:
            return chat.invoke(prompt).content
        log("prompt", prompt)
        try:
            text = chat.invoke(prompt).content
        except Exception as error:            # noqa: BLE001 — surface, then re-raise
            log("error", repr(error))
            raise
        log("response", text)
        return text

    return invoke, settings


def build_readers(
    *,
    enabled: bool = True,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    log: Optional[Log] = None,
) -> Readers:
    """The catalog resolver's roles: a prose reader and a claim comparer on one model.

    The reader is wrapped in :class:`~src.router.catalog.CachedProseReader`, so a passage
    is read once across the bundle's tables. ``enabled=False`` returns none of them, and
    the resolver runs its deterministic tiers alone.
    """
    if not enabled:
        return Readers()
    invoke, settings = invoker(
        CATALOG_MODULE, provider=provider, model=model, temperature=temperature, log=log
    )
    label = f"llm {settings.describe()}" + (" (debug)" if log else "")
    return Readers(CachedProseReader(LLMProseReader(invoke)), LLMClaimComparer(invoke), label)


def build_judges(
    *,
    enabled: bool = True,
    batch: bool = True,
    workers: int = 1,
    refresh_tool_cache: bool = False,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    log: Optional[Log] = None,
) -> Judges:
    """The router's three judges on one model, or none of them.

    They share a model and a set of options because they are three shapes of the same
    layer-4b decision, not three settings to keep in step. ``batch=False`` asks about one
    field per call — the comparison worth running against a labeled sheet —
    and ``workers`` issues calls concurrently.
    """
    if not enabled:
        return Judges()
    invoke, settings = invoker(
        JUDGE_MODULE, provider=provider, model=model, temperature=temperature, log=log
    )
    options: dict[str, Any] = {"batch": batch, "max_workers": workers}
    detail = "per-field" if not batch else "grouped"
    if workers > 1:
        detail += f", {workers} at a time"
    if refresh_tool_cache:
        detail += ", tool cache refreshed"
    if log:
        detail += ", debug"
    return Judges(
        tools=LLMToolMatcher(
            invoke, **options, cache_dir=TOOL_MATCH_CACHE, model=settings.describe(),
            refresh=refresh_tool_cache,
        ),
        columns=LLMColumnMatcher(invoke, **options),
        passages=LLMPassageReader(invoke, **options),
        label=f"{settings.describe()} ({detail})",
    )
