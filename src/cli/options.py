"""Flags the commands share, and the pipeline objects they build.

Defined once because they are the same choices in every entry point: the eval harness
grades what the router command runs, so a flag added here reaches both rather than being
restated and drifting.

An option group is part of the surface, not decoration: ``--help`` prints the groups, and
the app builds its form from them.
"""

from __future__ import annotations

import argparse
from typing import Optional

from rich.console import Console

from src.config import PROVIDER_CONFIGS, llm_settings
from src.pipelines.models import (
    CATALOG_MODULE,
    JUDGE_MODULE,
    Judges,
    Log,
    Readers,
    build_judges,
    build_readers,
)

_RULES = {"prompt": "[yellow]LLM prompt", "response": "[green]LLM response",
          "error": "[red]LLM error"}


def console_log(console: Console) -> Log:
    """Print each prompt, reply and error — what ``--debug`` shows.

    A role treats a failed call as an abstention, which reads on the artifact exactly
    like a role that considered the evidence and refused it. Those two are worth
    telling apart, so an error is printed before it is re-raised.
    """
    def log(kind: str, text: str) -> None:
        console.rule(_RULES.get(kind, kind))
        console.print(text, style="dim" if kind == "prompt" else None)

    return log


def add_model_options(group, module: str, *, backing: str) -> None:
    """Provider, model and temperature for one module, defaulting to its configuration.

    The defaults are read here, so ``--help`` and the app's form show what a run would
    actually use and an override is visibly an override.
    """
    configured = llm_settings(module)
    group.add_argument("--provider", choices=list(PROVIDER_CONFIGS),
                       default=configured.provider,
                       help=f"provider backing {backing} (default: {configured.provider})")
    group.add_argument("--model", default=configured.model,
                       help=f"model backing {backing} (default: {configured.model})")
    group.add_argument("--temperature", type=float, default=configured.temperature,
                       help=f"sampling temperature for {backing} (default: "
                            f"{configured.temperature})")


def add_judge_options(group) -> None:
    """How the router's judges are called — shared by the route command and the eval."""
    group.add_argument("--judge-workers", type=int, default=1, metavar="N",
                       help="issue the judges' calls N at a time. A self-hosted endpoint "
                            "batches concurrent requests internally, so this is usually "
                            "the largest win on a slow model (default: 1)")
    group.add_argument("--no-judge-batch", action="store_true",
                       help="ask about one field per call instead of many. Slower, but "
                            "each field is judged independently — the comparison worth "
                            "running against a labeled sheet")
    group.add_argument("--refresh-tool-cache", action="store_true",
                       help="ask the tool matcher again instead of reusing its saved "
                            "answers, and save the new answers in their place")


def judges_from_args(
    args: argparse.Namespace, console: Optional[Console] = None
) -> Judges:
    """The router's judges as the flags describe them (:mod:`src.pipelines.models`)."""
    debug = getattr(args, "debug", False) and console is not None
    return build_judges(
        enabled=args.llm_candidate_judge,
        batch=not args.no_judge_batch,
        workers=args.judge_workers,
        refresh_tool_cache=getattr(args, "refresh_tool_cache", False),
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
        log=console_log(console) if debug else None,
    )


def readers_from_args(
    args: argparse.Namespace, console: Optional[Console] = None
) -> Readers:
    """The catalog resolver's roles as the flags describe them."""
    debug = getattr(args, "debug", False) and console is not None
    return build_readers(
        enabled=args.llm_reader,
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
        log=console_log(console) if debug else None,
    )


#: Named here so a command's help can say which module's configuration it defaults to.
CATALOG_BACKING = f"--llm-reader (LLM_*_{CATALOG_MODULE})"
JUDGE_BACKING = f"--llm-candidate-judge (LLM_*_{JUDGE_MODULE})"
