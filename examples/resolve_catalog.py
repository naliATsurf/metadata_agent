"""Example: resolve a bundle's columns into a described catalog, and show the work.

Catalog resolution (layer 3) turns opaque column names into *described* columns by
harvesting meanings from the other files in the bundle — a codebook, a README —
and cross-checking each borrowed claim against the column's actual values. This is
standard-agnostic: it describes the data, independent of any metadata schema.

This example resolves a bundle and prints, per column: the resolved meaning, *how*
it was resolved (codebook table > glossary or prose read > value prior), the
confidence, the citation, and any conflicts or corroboration surfaced along the way.

Usage:

    # default: the router_test bundle (observations + a codebook with a Kelvin trap)
    python examples/resolve_catalog.py

    # any bundle; mark codebook CSVs as dictionaries (sources, not data tables)
    python examples/resolve_catalog.py --bundle data/sample/sharetrait_preprocessed/TRADAT031
    python examples/resolve_catalog.py --bundle mydir --dictionary variables.csv

    # read free *narrative* prose with the configured LLM (reads what no codebook,
    # table or glossary, covers); --doc restricts to one document, e.g. a
    # natural-language readme
    python examples/resolve_catalog.py --doc readme_hard.txt --llm-reader

    # add --debug to log every LLM prompt and raw response (and surface an error the
    # reader would otherwise swallow into a silent abstention)
    python examples/resolve_catalog.py --doc readme_hard.txt --llm-reader --debug

    # the reader uses this module's own model (LLM_*_CATALOG_RESOLVER in .env,
    # falling back to the global LLM_PROVIDER / LLM_MODEL), overridable per run
    python examples/resolve_catalog.py --llm-reader --provider openai --model gpt-4o-mini
    python examples/resolve_catalog.py --llm-reader --temperature 0.2

    # save the resolution, for the field router to route without resolving again
    python examples/resolve_catalog.py --out catalog.json
    python examples/field_router_plan.py --catalog catalog.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console

from src.config import PROVIDER_CONFIGS, llm_settings
from src.context import create_context
from src.router import (
    Bundle,
    CachedProseReader,
    ClaimComparer,
    LLMClaimComparer,
    LLMProseReader,
    NONE,
    ProseReader,
    ResolvedBundle,
    discover_bundle,
    render_catalog,
    select,
    resolve_bundle,
    resolve_catalog,
)

#: This example's module name for per-module LLM selection (see src/config.py).
LLM_MODULE = "CATALOG_RESOLVER"

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = REPO / "data/sample/sharetrait_preprocessed/TRADAT031"


def resolve(
    bundle: Bundle,
    dicts: List[Path],
    docs: List[Path],
    prose_reader: ProseReader | None = None,
    reader_label: str = "off",
    claim_comparer: ClaimComparer | None = None,
) -> ResolvedBundle:
    """Resolve the bundle's data tables against the chosen codebooks / documents."""
    table_ctx = [create_context(str(p), name=p.stem) for p in bundle.tables]
    sources = [create_context(str(p), name=p.stem) for p in (*dicts, *docs)]
    models = {"prose_reader": prose_reader, "claim_comparer": claim_comparer}
    if len(table_ctx) == 1:
        catalog = resolve_catalog(table_ctx[0], sources=sources, **models)
    else:
        catalog = resolve_bundle(table_ctx, sources=sources, **models)
    return ResolvedBundle(bundle.root, bundle.tables, dicts, docs, reader_label, catalog)


def _logging_invoke(model, console: Console):
    """Wrap a chat model's invoke to log each prompt and raw response (for --debug).

    Because LLMProseReader takes a plain ``prompt -> text`` callable, this is the seam
    to watch the reader without editing the resolver: it prints the prompt and the raw
    model text, and surfaces an error the reader would otherwise swallow into a silent
    abstention (``read_many`` returns ``{}`` on exception).
    """
    def invoke(prompt: str) -> str:
        console.rule("[yellow]LLM prompt")
        console.print(prompt, style="dim")
        try:
            text = model.invoke(prompt).content
        except Exception as e:               # noqa: BLE001 — surface then re-raise
            console.rule("[red]LLM error")
            console.print(repr(e), style="red")
            raise
        console.rule("[green]LLM response")
        console.print(text)
        return text
    return invoke


def build_reader(
    args, console: Console
) -> Tuple[ProseReader | None, ClaimComparer | None, str]:
    """Pick the models from the flags: with --llm-reader, an LLM prose reader and an LLM
    claim comparer on the same model; else neither.

    The model is built from the provider, model, and temperature on ``args`` (each
    defaulting to the repo's configuration). The reader is wrapped in CachedProseReader
    so a document is read once across the bundle's tables; the comparer judges which
    differently worded claims about a column mean the same. With --debug the model's
    invoke is wrapped to log prompts and responses.

    The returned label names the model, so a run's output records what produced it
    rather than leaving it to the environment.
    """
    if args.llm_reader:
        from src.config import create_llm_for  # lazy: pulls provider SDKs when used
        settings = llm_settings(
            LLM_MODULE,
            provider=args.provider,
            model=args.model,
            temperature=args.temperature,
        )
        model = create_llm_for(LLM_MODULE, **vars(settings))
        invoke = (
            _logging_invoke(model, console)
            if args.debug
            else lambda prompt: model.invoke(prompt).content
        )
        label = f"llm {settings.describe()}"
        return (
            CachedProseReader(LLMProseReader(invoke)),
            LLMClaimComparer(invoke),
            f"{label} (debug)" if args.debug else label,
        )
    return None, None, "off"


def build_parser() -> argparse.ArgumentParser:
    """The example's argument surface, built separately so a UI can render it.

    Keeping the parser out of :func:`main` lets a front end enumerate the flags
    (and their help text) to build a form, instead of duplicating them.
    """
    ap = argparse.ArgumentParser(description="Resolve a bundle's columns and show the evidence.")

    # Groups are part of the argument surface: --help prints them, and a UI built from
    # this parser lays its form out by them.
    source = ap.add_argument_group(
        "Input", "The bundle, and which of its discovered sources to resolve from."
    )
    tier = ap.add_argument_group(
        "Prose reader", "Whether a model reads the narrative no codebook covers."
    )
    model = ap.add_argument_group(
        "LLM prose reader model",
        "Backing --llm-reader; each defaults to this module's configuration.",
    )

    source.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="bundle directory")
    source.add_argument("--dictionary", action="append", default=None,
                    help="codebooks to use, by filename (repeatable). Omit to use every "
                         f"codebook found in the bundle; pass '{NONE}' to use none")
    tier.add_argument("--llm-reader", action="store_true",
                    help="enable the LLM prose reader (reads free narrative) using the "
                         "configured model, for columns no codebook covers; the same "
                         "model judges which differently worded claims agree")
    source.add_argument("--doc", action="append", default=None,
                    help="documents to use, by filename (repeatable). Omit to use every "
                         f"document found in the bundle; pass '{NONE}' to use none")
    tier.add_argument("--debug", action="store_true",
                    help="with --llm-reader, log each prompt and raw model response (and "
                         "surface an error the reader would otherwise swallow)")
    source.add_argument("--out", type=Path, default=None,
                    help="write the resolution as JSON, for examples/field_router_plan.py "
                         "--catalog")
    # This module's configured model, as the flags' defaults — so --help and the
    # UI show what a run would actually use, and an override is visibly an override.
    configured = llm_settings(LLM_MODULE)
    model.add_argument("--provider", choices=list(PROVIDER_CONFIGS), default=configured.provider,
                    help="provider backing --llm-reader (default: "
                         "LLM_PROVIDER_CATALOG_RESOLVER, else LLM_PROVIDER)")
    model.add_argument("--model", default=configured.model,
                    help="model backing --llm-reader (default: "
                         "LLM_MODEL_CATALOG_RESOLVER, else LLM_MODEL)")
    model.add_argument("--temperature", type=float, default=configured.temperature,
                    help="sampling temperature for --llm-reader (default: "
                         "LLM_TEMPERATURE_CATALOG_RESOLVER)")
    return ap


def run(args: argparse.Namespace, console: Console) -> ResolvedBundle:
    """Resolve the bundle described by ``args`` and print the evidence to ``console``.

    Everything is written through ``console``, so a caller can pass a
    ``Console(record=True)`` and capture the whole run instead of printing it.
    The resolution is returned as well — the catalog and the files it came from —
    for callers that render it themselves or hand it on to the router.
    """
    if not args.bundle.exists() or not any(args.bundle.iterdir()):
        raise SystemExit(f"Bundle {args.bundle} is missing or empty.")

    try:
        bundle = discover_bundle(args.bundle)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    tables, codebooks, documents = bundle.tables, bundle.codebooks, bundle.documents
    dicts = select(codebooks, args.dictionary)
    docs = select(documents, args.doc)
    reader, comparer, reader_kind = build_reader(args, console)
    console.print(f"[bold]bundle:[/] {args.bundle}")
    console.print(f"tables: {[p.name for p in tables]}   "
                  f"dictionaries: {[p.name for p in dicts] or 'none'}   "
                  f"docs: {[p.name for p in docs] or 'none'}   "
                  f"reader: {reader_kind}")
    excluded = [p.name for p in (*codebooks, *documents) if p not in (*dicts, *docs)]
    if excluded:
        console.print(f"[dim]discovered but not used: {excluded}[/]")
    console.print("")

    resolved = resolve(
        bundle, dicts, docs, prose_reader=reader, reader_label=reader_kind, claim_comparer=comparer
    )
    render_catalog(resolved.catalog, console)
    return resolved


def main() -> None:
    args, console = build_parser().parse_args(), Console()
    resolved = run(args, console)
    # Written here, not in run(): producing a file is a command-line act, and a UI
    # driving run() hands the resolution on in memory.
    if args.out:
        console.print(f"\nWrote {resolved.save(args.out)}.")


if __name__ == "__main__":
    main()
