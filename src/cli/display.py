"""Rendering a run to a terminal — the command line's half of the field-driven path.

The pipeline returns artifacts; this prints them. Everything goes through a ``Console``,
so a caller can pass ``Console(record=True)`` and capture a whole run rather than print
it, which is how the app shows a command's output.

Nothing here decides anything: every line is read off the routing artifact, which
records each step already. An intermediate you can only see with a debug flag on is an
intermediate the artifact should have been carrying.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import yaml
from rich.console import Console

from src.core.schemas import Plan
from src.pipelines.field_driven import FieldDrivenRun
from src.router import FieldPlan

#: Where the command writes the artifacts it is asked to save.
OUT = Path(__file__).resolve().parents[2] / "data" / "sample_output"


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
    units do not fit, and why, the candidates, the judge's verdict, quotes and
    citations — so this reads the plan rather than instrumenting the router. Which is
    the point: an intermediate you can only see with a debug flag on is an intermediate
    the artifact should have been carrying.

    The distinction to look for is *why* a field is unanswered: nothing retrieved (no
    judges ran) and everything refused (a judge note) both end at ``unanswered``, and
    they call for opposite fixes.
    """
    console.print("\n[bold]Working (per field)[/]")
    for path, routing in field_plan.routings.items():
        console.print(f"\n[bold]{path}[/] [dim]— {routing.query}[/]")
        console.print(f"  bucket={routing.bucket} assurance={routing.assurance}")
        if routing.tool_choice:
            console.print(f"  [magenta]tool[/]  {routing.tool_choice} — {routing.tool_note or ''}")
        for arguments in routing.tool_arguments:
            bound = ", ".join(f"{name}={value}" for name, value in arguments.items())
            console.print(f"  [magenta]runs with[/] {bound or 'the whole context'}")
        for reason in routing.mismatches:
            console.print(f"  [yellow]does not fit[/] {reason}")
        for note in routing.varies:
            console.print(f"  [dim]varies[/] {note}")
        if not routing.candidates and not routing.judge_note:
            console.print("  [dim]no candidates retrieved[/]")
        for rank, c in enumerate(routing.candidates, 1):
            mark = "[green]→[/]" if rank == 1 and routing.status == "routed" else " "
            console.print(
                f"  {mark} {rank}. {c.resource or '—'}:{c.locator} "
                f"[dim]{c.kind} score={c.score:.2f}[/]"
            )
        if routing.judge_note:
            console.print(f"  [cyan]judge[/] {routing.judge_choice or 'none'} — {routing.judge_note}")
        for quote, citation in zip(routing.judge_quotes, routing.citations):
            where = f"[cyan]cite[/] {citation}" if citation else "[red]not located[/]"
            console.print(f"  [cyan]quote[/] {quote!r}\n        {where}")


def print_plan(plan: Plan, console: Console) -> None:
    """The compiled plan: one extraction task per table."""
    console.print("\n[bold]2. Compiled plan (one extraction task per table)[/]")
    for i, t in enumerate(plan.steps):
        scope = t.target_resources or ["<context>"]
        console.print(
            f"[{i}] task={t.task:<26} player={t.player:<19} "
            f"topology={t.topology or '-':<7} scope={scope} fields={len(t.fields)}"
        )


def write_artifacts(run: FieldDrivenRun, console: Console) -> List[Path]:
    """Persist the field plan and the compiled plan, and say where they went.

    Kept out of ``run``: producing artifacts is a command-line act. An app driving the
    pipeline is trying inputs, not building outputs, and should not overwrite the
    checked-in plans on every click.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    field_plan_path = OUT / f"{run.standard}_field_plan.yaml"
    plan_path = OUT / f"{run.standard}_compiled_plan.yaml"
    field_plan_path.write_text(_yaml(run.field_plan.to_dict()))
    plan_path.write_text(_yaml({"plan": run.plan.model_dump()}))
    console.print(f"\nWrote {field_plan_path} and {plan_path}.")
    return [field_plan_path, plan_path]
