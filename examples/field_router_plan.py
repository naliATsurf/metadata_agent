"""Example: route a resolved catalog into a field-driven extraction plan.

The router's half of the field-driven pipeline, deterministic and with no LLM calls
unless the judges are asked for:

    (layer 3, examples/resolve_catalog.py)  →  route_fields  →  compile_field_plan
                                               (layer 4)        (layer 5)

It starts from a *resolution* — the catalog examples/resolve_catalog.py saves, together
with the bundle files it was resolved from — rather than resolving again, so the two
stages are run, inspected, and varied separately. A real repository is **many tables**:
the fields of one schema are answered by columns in *different* CSVs, so each field is
routed to whichever table's column (or document span) answers it, and the routing is
compiled into a `Plan` whose extraction tasks are grouped per table.

Usage:

    # resolve once, then route the saved resolution
    python examples/resolve_catalog.py --out catalog.json
    python examples/field_router_plan.py --catalog catalog.json

    # another standard, with a model matching columns and reading passages for each field
    python examples/field_router_plan.py --catalog catalog.json \\
        --standard field_router_test --llm-candidate-judge

The documents routed against are the ones the catalog was resolved from; ``--search-doc``
narrows that to a subset without resolving again, which is how a bundle carrying rival
variants of one README is routed against one of them at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import yaml

# Make the repo importable when run directly (python examples/field_router_plan.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.console import Console

from src.context import create_context
from src.core.schemas import Plan
from src.router import (
    NONE,
    FieldPlan,
    ResolvedBundle,
    compile_field_plan,
    route_fields,
    select,
)
from src.config import llm_settings, PROVIDER_CONFIGS
from src.router.column_matcher import ColumnMatcher, LLMColumnMatcher
from src.router.passage_reader import LLMPassageReader, PassageReader
from src.standards import METADATA_STANDARDS, get_schema_for_standard

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "sample_output"

DEFAULT_STANDARD = "sharetrait_basic_no_trait"

#: Which per-module LLM configuration the judges (column matcher, passage reader) draw from. Set
#: LLM_PROVIDER_CANDIDATE_JUDGE / LLM_MODEL_CANDIDATE_JUDGE / LLM_TEMPERATURE_CANDIDATE_JUDGE
#: in .env to point it somewhere other than the global default.
LLM_MODULE = "CANDIDATE_JUDGE"


def build_plan(
    resolved: ResolvedBundle,
    standard: str,
    candidates: int = 5,
    matcher: ColumnMatcher | None = None,
    reader: PassageReader | None = None,
    documents: List[Path] | None = None,
) -> Tuple[FieldPlan, Plan]:
    """The core: route the resolved catalog → compile.

    ``documents`` narrows which of the resolution's documents are *routed*, without
    resolving again. The two document choices answer different questions: the
    resolver's picks what describes the columns, this picks what is searched for the
    fields no column answers. A bundle carrying three variants of one README —
    a short one, a prose rewrite, a whole methods section — routes them as three
    rival sources unless one is named here.
    """
    schema = get_schema_for_standard(standard)
    if schema is None:
        raise SystemExit(f"Unknown standard {standard!r}.")

    routed = resolved.documents if documents is None else documents
    doc_ctx = [create_context(str(p), name=p.stem) for p in routed]
    field_plan = route_fields(                                     # layer 4 (+ 4b)
        schema, catalog=resolved.catalog, docs=doc_ctx, k=candidates,
        matcher=matcher, reader=reader,
    )
    plan = compile_field_plan(field_plan)                          # layer 5
    return field_plan, plan


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _yaml(obj) -> str:
    """Clean YAML, normalizing tuples (span locators) to lists via a JSON round-trip."""
    return yaml.safe_dump(json.loads(json.dumps(obj, default=str)), sort_keys=False, width=100)


def print_routing(field_plan: FieldPlan, console: Console) -> None:
    """Which table, column, or document span answers each schema field."""
    console.print("\n[bold]1. Field routing (which table/column answers each field)[/]")
    console.print(f"{'field':<28}{'bucket':<22}{'assurance':<10}source", style="dim")
    for path, r in field_plan.routings.items():
        console.print(f"{path:<28}{r.bucket:<22}{r.assurance:<10}{_source_of(r)}")
    cov = field_plan.coverage()
    console.print(
        f"\n[bold]coverage:[/] {cov['routed']}/{cov['total']} routed, "
        f"unanswered={cov['unanswered']}, by_bucket={cov['by_bucket']}"
    )


def _source_of(routing) -> str:
    """The routing's top candidate, rendered as ``resource:locator``."""
    if not routing.candidates:
        return "—"
    c = routing.candidates[0]
    return f"{c.resource}:{c.locator}" if c.resource else str(c.locator)


def print_working(field_plan: FieldPlan, console: Console) -> None:
    """Each field's working: what was offered, what was refused, and on what evidence.

    The routing artifact records every step already — the candidates whose type or
    units do not fit, and why, the ranked candidates, the judge's verdict, quote and citation — so
    this reads the plan rather than instrumenting the router. Which is the point: an
    intermediate you can only see with a debug flag on is an intermediate the artifact
    should have been carrying.

    The distinction to look for is *why* a field is unanswered: nothing retrieved (no
    candidates) and everything refused (candidates listed, no choice) both end at
    ``unanswered``, and they call for opposite fixes.
    """
    console.print("\n[bold]Working (per field)[/]")
    for path, routing in field_plan.routings.items():
        console.print(f"\n[bold]{path}[/] [dim]— {routing.query}[/]")
        console.print(f"  bucket={routing.bucket} assurance={routing.assurance}")
        for reason in routing.mismatches:
            console.print(f"  [yellow]type/unit mismatch[/] {reason}")
        if not routing.candidates:
            console.print("  [dim]no candidates retrieved[/]")
        for rank, c in enumerate(routing.candidates, 1):
            mark = "[green]→[/]" if rank == 1 and routing.status == "routed" else " "
            console.print(
                f"  {mark} {rank}. {c.resource or '—'}:{c.locator} "
                f"[dim]{c.kind} score={c.score:.2f}[/]"
            )
        if routing.judge_note:
            console.print(f"  [cyan]judge[/] {routing.judge_choice or 'none'} — {routing.judge_note}")
        if routing.judge_quote:
            grounded = "located" if routing.judge_grounded else "[red]not located[/]"
            console.print(f"  [cyan]quote[/] ({grounded}) {routing.judge_quote!r}")
        if routing.citation:
            console.print(f"  [cyan]cite[/]  {routing.citation}")


def print_plan(plan: Plan, console: Console) -> None:
    """The compiled plan: one extraction task per table."""
    console.print("\n[bold]2. Compiled plan (one extraction task per table)[/]")
    for i, t in enumerate(plan.steps):
        scope = t.target_resources or ["<context>"]
        console.print(
            f"[{i}] task={t.task:<26} player={t.player:<19} "
            f"topology={t.topology or '-':<7} scope={scope} fields={len(t.fields)}"
        )


@dataclass(frozen=True)
class RouterResult:
    """Everything one run produced, for a caller that renders it itself."""

    resolved: ResolvedBundle
    field_plan: FieldPlan
    plan: Plan
    standard: str


def build_parser() -> argparse.ArgumentParser:
    """The example's argument surface, built separately so a UI can render it."""
    ap = argparse.ArgumentParser(
        description="Route a resolved catalog into a field-driven plan."
    )
    source = ap.add_argument_group(
        "Input", "The resolution to route: a catalog and the files it came from."
    )
    target = ap.add_argument_group(
        "Metadata standard", "The schema whose fields are routed."
    )
    routing = ap.add_argument_group(
        "Routing", "How many candidates the router keeps per field."
    )
    model = ap.add_argument_group(
        "LLM candidate judge model",
        "Backing --llm-candidate-judge; each defaults to that module's configuration.",
    )

    source.add_argument("--catalog", type=Path, required=True,
                        help="a resolution saved by examples/resolve_catalog.py --out")
    source.add_argument("--search-doc", action="append", default=None,
                        help=f"search only these of the resolution's documents by "
                             f"filename (repeatable); '{NONE}' for none. Deliberately "
                             "not the resolver's --doc: that chose what *described the "
                             "columns*, this chooses what is *searched to answer* the "
                             "fields no column answers. Narrowing here re-routes; it "
                             "does not resolve again")
    target.add_argument("--standard", default=DEFAULT_STANDARD,
                        choices=sorted(METADATA_STANDARDS),
                        help="metadata standard whose fields are routed")
    routing.add_argument("--candidates", type=int, default=5,
                         help="how many ranked candidates to keep per field. The router "
                              "proposes a set and the executor picks from it, so this is "
                              "the recall budget, not a display setting")
    routing.add_argument("--debug", action="store_true",
                         help="show each field's working: which candidates do not fit on type or units, every "
                              "candidate the judge was offered with its score, and the "
                              "verdict, quote and citation that came back. With "
                              "--llm-candidate-judge it also logs each prompt and raw "
                              "response, and surfaces an error the judge would otherwise "
                              "turn into a silent abstention")
    routing.add_argument("--llm-candidate-judge", action="store_true",
                         help="let an LLM decide what answers each field, or that nothing "
                              "does: a column matcher over the whole catalog, then a "
                              "passage reader over each retrieved passage. Without it rank 1 "
                              "wins on BM25 score, which over-answers when schema and data "
                              "were authored apart")

    configured = llm_settings(LLM_MODULE)
    model.add_argument("--provider", choices=list(PROVIDER_CONFIGS),
                       default=configured.provider,
                       help=f"provider backing --llm-candidate-judge (default: {configured.provider})")
    model.add_argument("--model", default=configured.model,
                       help=f"model backing --llm-candidate-judge (default: {configured.model})")
    model.add_argument("--temperature", type=float, default=configured.temperature,
                       help="sampling temperature for --llm-candidate-judge (default: "
                            f"{configured.temperature})")
    model.add_argument("--judge-workers", type=int, default=1, metavar="N",
                       help="issue the judge's calls N at a time. A self-hosted "
                            "endpoint batches concurrent requests internally, so this "
                            "is usually the largest win on a slow model (default: 1)")
    model.add_argument("--no-judge-batch", action="store_true",
                       help="ask about one field per call instead of many. Slower, but "
                            "each field is judged independently — the comparison worth "
                            "running against a labeled sheet")
    return ap


def _logging_invoke(model, console: Console):
    """Wrap a chat model's invoke to log each prompt and raw response (for --debug).

    The same seam the resolver uses: both judges take a plain
    ``prompt -> text`` callable, so the judge can be watched without editing the
    router. It also surfaces an error the judge would otherwise turn into a silent
    abstention — ``_referee`` treats a failed call as "no usable answer", which reads
    on the routing exactly like a judge that considered the candidates and refused
    them. Those two are worth telling apart.
    """
    def invoke(prompt: str) -> str:
        console.rule("[yellow]Judge prompt")
        console.print(prompt, style="dim")
        try:
            text = model.invoke(prompt).content
        except Exception as e:               # noqa: BLE001 — surface then re-raise
            console.rule("[red]Judge error")
            console.print(repr(e), style="red")
            raise
        console.rule("[green]Judge response")
        console.print(text)
        return text
    return invoke


def build_judges(
    args: argparse.Namespace, console: Console | None = None
) -> Tuple[ColumnMatcher | None, PassageReader | None, str]:
    """Build the column matcher and passage reader from the flags, and a label.

    Both judges share one model and one set of flags: they are two shapes of the same
    layer-4b decision, not two settings a user should have to keep in step.

    The model is constructed lazily — importing a provider SDK is only worth it when
    a judge is actually asked for, and the whole deterministic path must stay
    runnable with no credentials configured. With ``--debug`` and a ``console``, the
    model's invoke is wrapped to log prompts and responses.
    """
    if not args.llm_candidate_judge:
        return None, None, "off"
    from src.config import create_llm_for   # lazy: pulls provider SDKs when used

    settings = llm_settings(
        LLM_MODULE, provider=args.provider, model=args.model,
        temperature=args.temperature,
    )
    model = create_llm_for(LLM_MODULE, **vars(settings))
    debug = getattr(args, "debug", False) and console is not None
    invoke = (
        _logging_invoke(model, console) if debug
        else (lambda prompt: model.invoke(prompt).content)
    )
    options = dict(batch=not args.no_judge_batch, max_workers=args.judge_workers)
    matcher = LLMColumnMatcher(invoke, **options)
    reader = LLMPassageReader(invoke, **options)
    detail = "per-field" if args.no_judge_batch else "grouped"
    if args.judge_workers > 1:
        detail += f", {args.judge_workers} at a time"
    if debug:
        detail += ", debug"
    return matcher, reader, f"{settings.describe()} ({detail})"


def run(
    args: argparse.Namespace,
    console: Console,
    resolved: ResolvedBundle | None = None,
) -> RouterResult:
    """Route and compile, reporting through ``console``.

    The resolution is read from ``args.catalog``, unless a caller that already holds
    one in memory passes it as ``resolved`` — the UI hands on the catalog its resolver
    page produced, without a file in between. Returns what it built as well, so a
    caller can render the routing itself rather than read the printed tables.
    """
    if resolved is None:
        if not args.catalog.is_file():
            raise SystemExit(
                f"No resolution at {args.catalog}. Save one with "
                f"examples/resolve_catalog.py --out {args.catalog}, then rerun."
            )
        resolved = ResolvedBundle.load(args.catalog)
    matcher, reader, judge_label = build_judges(args, console)

    console.print(f"[bold]bundle:[/] {resolved.root}")
    console.print(f"standard: {args.standard}")
    console.print(
        f"catalog:  {len(resolved.catalog.columns)} columns from "
        f"{[p.name for p in resolved.tables]}  (prose reader: {resolved.reader})"
    )
    routed_docs = select(resolved.documents, args.search_doc)
    held_back = [p.name for p in resolved.documents if p not in routed_docs]
    console.print(
        f"docs:     {[p.name for p in routed_docs] or 'none'}  "
        f"candidates={args.candidates}  candidate-judge={judge_label}"
        + (f"  (not routed: {held_back})" if held_back else "")
    )

    field_plan, plan = build_plan(
        resolved, args.standard, candidates=args.candidates,
        matcher=matcher, reader=reader,
        documents=routed_docs,
    )
    print_routing(field_plan, console)
    if args.debug:
        print_working(field_plan, console)
    print_plan(plan, console)

    return RouterResult(resolved, field_plan, plan, args.standard)


def write_artifacts(result: RouterResult, console: Console) -> List[Path]:
    """Persist the field plan and the compiled plan, and say where they went.

    Kept out of :func:`run` so that producing artifacts is a command-line act. A UI
    driving ``run`` is trying inputs, not building outputs, and should not overwrite
    the checked-in plans on every click.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    field_plan_path = OUT / f"{result.standard}_field_plan.yaml"
    plan_path = OUT / f"{result.standard}_compiled_plan.yaml"
    field_plan_path.write_text(_yaml(result.field_plan.to_dict()))
    plan_path.write_text(_yaml({"plan": result.plan.model_dump()}))
    console.print(f"\nWrote {field_plan_path} and {plan_path}.")
    return [field_plan_path, plan_path]


def main() -> None:
    console = Console()
    write_artifacts(run(build_parser().parse_args(), console), console)


if __name__ == "__main__":
    main()
