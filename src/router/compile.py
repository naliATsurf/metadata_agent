"""Layer 5 — compile a FieldPlan into an executable Plan of Tasks.

The router (layer 4) decides *where* each field is answered; the compiler turns
that routing artifact into the execution form the existing `PlanExecutor` already
runs — a `Plan` of `Task`s. Nothing downstream of here changes: the compiler emits
the same `Plan`/`Task` shape the source-driven planner does, so `PlanExecutor` and
`StepExecutor` run unchanged.

**Linchpin 1 — field identity survives compilation.** The `FieldPlan` is the
source of truth; a `Task` is only its execution form. Each compiled task carries
`fields=[…]` (the schema paths it must fill) so the field → task → result
correspondence is *explicit*, never reconstructed by heuristics afterwards. Lose
that and we are back to post-hoc matching and the FieldPlan was theatre.

What the compiler does, and only that:

- **Groups** routings that share an extractor *and an assurance tier* — the same
  (bucket, resource, tier) — into one task, so fields answered from the same place
  are extracted together instead of one survey per field. The tier is part of the
  key so a high-assurance field is never dragged into a debate just because a
  contested field happens to share its resource.
- **Caps each group by a token budget.** Grouping risks re-introducing the flood at
  the group level, so a group whose seeded candidates exceed the budget is split
  into several tasks.
- **Attaches each task's candidates** as `field_bindings` — for each field, its
  *ranked* candidate set: the curated slice its fields need, not a whole-context
  survey (*retrieval is the curation*). This is the single carrier of candidates on a
  task; there is no separate deduped pool.
- **Proposes a set per field; it does not commit.** Each task carries the router's
  ranked candidate set per field, not a single hard-bound locator, and its `task` is
  a short action name — *what to do* (select the fitting candidate and extract) is a
  general policy rendered at execution from `field_bindings`, not a stored paragraph.
  The executor chooses which candidate actually answers each field: the router
  proposes, the evidence disposes. An answer the lexical router ranked second (a
  right span it under-scored) stays reachable, so correctness depends on the router's
  **recall**, not its precision@1. Assurance travels as **provisional**, confirmed by
  the verify pass, and the field → candidate binding is carried *structurally* so the
  verifier never parses an instruction string.
- **Chooses a topology per task from assurance**: a single extractor for
  high-assurance computed fields, debate for contested/low-assurance ones (the
  debate machinery's best real use — resolving disagreement, not re-surveying).
  Because grouping already separates the tiers, each task is homogeneous — no
  high-assurance field pays for a neighbor's contest, so debate stays targeted.
- **Fans in to one assembly task** that depends on every extraction task and emits
  the final metadata record, replacing the monolithic synthesis step.

An **unresolved** field gets *no* extraction task — nothing can answer it. It is
still named in the assembly task's `fields`, so the record carries an explicit
null rather than a fabricated value. That is coverage honored at execution time.

Out of scope (M5): the reconcile/verify pass (linchpin 2) that confirms a produced
value actually traces to its routed candidate. The compiler only lays out the
work; `attribute_field` as its verifier is the next milestone.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from src.core.schemas import Plan, Task
from src.router.route import FieldPlan, FieldRouting

# Default player role per bucket. Roles request *capabilities* (toolsets), never a
# standard's vocabulary, so a bucket maps to a role by what it must *do*, not by
# field names: structural and column extraction are tabular analysis; narrative
# extraction reads prose (the seeded spans) and writes fields. Overridable so the
# mapping is a default, not a hardcoding.
_BUCKET_PLAYER = {
    "structural": "data_analyst",
    "ambiguous_structural": "data_analyst",
    "narrative": "metadata_specialist",
}

# `task` is a short *action identifier* (like the source-driven planner's
# 'get_row_count'), not a prose instruction. What to do is carried structurally in
# `field_bindings` (the ranked candidates per field) and rendered into a player
# prompt at execution time — not stored as a paragraph on the task.
_TASK_NAME = {
    "structural": "compute_structural_fields",
    "ambiguous_structural": "extract_column_fields",
    "narrative": "extract_narrative_fields",
}
_ASSEMBLY_TASK_NAME = "assemble_metadata_record"

# The fan-in synthesizer. Depends on every extraction task and emits the record.
_ASSEMBLY_PLAYER = "metadata_generator"
_FINAL_ARTIFACT = "final_metadata"

# Character budget for one task's seeded candidate payload. A group whose seeded
# candidates exceed this is split into several tasks, so grouping fields that share
# an extractor does not re-create the flooding the field-driven design removes.
_DEFAULT_BUDGET = 2000


def compile_field_plan(
    field_plan: FieldPlan,
    *,
    budget: int = _DEFAULT_BUDGET,
    bucket_player: Optional[Dict[str, str]] = None,
    assembly_player: str = _ASSEMBLY_PLAYER,
) -> Plan:
    """Compile a routed :class:`FieldPlan` into an executable :class:`Plan`.

    Groups the routed fields by extractor, caps each group by ``budget``, seeds each
    task with its candidates, and fans in to one assembly task. Unresolved fields
    are skipped (no extraction task) but still named on the assembly task so the
    record nulls them explicitly. Deterministic: groups and fields are emitted in a
    stable order.
    """
    players = {**_BUCKET_PLAYER, **(bucket_player or {})}

    # Partition the routed fields by (bucket, resource, tier). Unresolved fields have
    # no extractor, so they are not grouped — they surface as explicit nulls at
    # assembly. Insertion order (schema order) is preserved for determinism.
    groups: "OrderedDict[Tuple[str, str, str], List[FieldRouting]]" = OrderedDict()
    for routing in field_plan.routings.values():
        if routing.status == "unresolved":
            continue
        groups.setdefault(_group_key(routing), []).append(routing)

    # Flatten to budget-capped chunks, then number them *globally*: two groups can
    # share a (bucket, resource) but differ in tier, so a per-group index would
    # collide on the output-artifact name. A single running index keeps them unique.
    chunks: List[Tuple[str, str, List[FieldRouting]]] = []
    for (bucket, resource, _topology), routings in groups.items():
        routings = sorted(routings, key=lambda r: r.field_path)
        for chunk in _split_by_budget(routings, budget):
            chunks.append((bucket, resource, chunk))

    steps: List[Task] = []
    extraction_outputs: List[str] = []
    for index, (bucket, resource, chunk) in enumerate(chunks):
        task = _extraction_task(bucket, resource, chunk, players, index)
        steps.append(task)
        extraction_outputs.extend(task.outputs)

    steps.append(_assembly_task(field_plan, extraction_outputs, assembly_player))
    return Plan(steps=steps)


# ---------------------------------------------------------------------------
# Grouping and budgeting
# ---------------------------------------------------------------------------


def _group_key(routing: FieldRouting) -> Tuple[str, str, str]:
    """The extractor *and tier* a field shares: bucket, resource, and topology tier.

    Structural fields bind to a whole-resource tool (candidate ``resource`` is
    empty), so they all share one group; column and span fields group by the
    resource that holds the column or document. The topology tier (from
    :func:`_topology_for`) splits a mixed group so a high-assurance field is not
    pulled into a contested sibling's debate.
    """
    resource = routing.candidates[0].resource if routing.candidates else ""
    return (routing.bucket, resource, _topology_for([routing]))


def _candidate_cost(routing: FieldRouting) -> int:
    """The seeded payload size a routing contributes — the length of its snippets."""
    return sum(len(c.snippet or "") for c in routing.candidates)


def _split_by_budget(
    routings: List[FieldRouting], budget: int
) -> List[List[FieldRouting]]:
    """Split a group into chunks whose seeded payload stays within ``budget``.

    Greedy: accumulate fields until the next would exceed the budget, then start a
    new chunk. A single field larger than the budget still gets its own chunk (it
    cannot be split further), so no field is ever dropped.
    """
    chunks: List[List[FieldRouting]] = []
    current: List[FieldRouting] = []
    running = 0
    for routing in routings:
        cost = _candidate_cost(routing)
        if current and running + cost > budget:
            chunks.append(current)
            current, running = [], 0
        current.append(routing)
        running += cost
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Task construction
# ---------------------------------------------------------------------------


def _topology_for(routings: List[FieldRouting]) -> str:
    """Single extractor when every field is high-assurance; debate otherwise.

    High-assurance computed fields need no debate — the computation is its own
    check. A contested or low-assurance field is exactly where the debate topology
    earns its cost: resolving disagreement between grounded sources.

    This is the single definition of the tier rule. It is used both to split groups
    (via :func:`_group_key`, one routing at a time) and to set each task's topology,
    so grouping and the per-task hint can never disagree.
    """
    return "single" if all(r.assurance == "high" for r in routings) else "debate"


def _field_bindings(routings: List[FieldRouting]) -> List[Dict[str, Any]]:
    """Per-field ranked candidate sets — the one structured carrier of candidates.

    Each entry keeps a field's *own* ranked candidate list (top-k, best-guess first)
    with its query and provisional assurance. So the executor chooses which candidate
    actually answers the field — the router proposes a set, it does not command a
    pick — an under-ranked but correct candidate stays reachable, and the verifier
    reads the binding structurally instead of parsing an instruction. ``assurance``
    is provisional: the router's grade, to be confirmed by the verify pass. This is
    the *only* place candidates live on a task (no separate deduped pool).
    """
    return [
        {
            "field": r.field_path,
            "query": r.query,
            "assurance": r.assurance,   # provisional — confirmed by the verify pass
            "candidates": [c.to_dict() for c in r.candidates],
        }
        for r in routings
    ]


def _artifact_name(bucket: str, resource: str, index: int) -> str:
    """A stable, unique output-artifact name for one extraction task."""
    slug = re.sub(r"[^a-z0-9]+", "_", (resource or "context").lower()).strip("_")
    return f"{bucket}__{slug}__{index}_findings"


def _extraction_task(
    bucket: str,
    resource: str,
    routings: List[FieldRouting],
    players: Dict[str, str],
    index: int,
) -> Task:
    fields = [r.field_path for r in routings]
    # Structural fields are context-level (empty target = all/context); column and
    # span fields target the resource that holds them.
    target = [] if bucket == "structural" or not resource else [resource]
    return Task(
        task=_TASK_NAME[bucket],
        player=players.get(bucket, _BUCKET_PLAYER["ambiguous_structural"]),
        rationale=(
            f"Field-driven routing sent {len(fields)} field(s) to the {bucket} "
            f"extractor for '{resource or 'context'}'. For each, select the candidate "
            "that answers it from its ranked options and extract, or report none."
        ),
        target_resources=target,
        fields=fields,
        field_bindings=_field_bindings(routings),
        topology=_topology_for(routings),
        outputs=[_artifact_name(bucket, resource, index)],
    )


def _assembly_task(
    field_plan: FieldPlan, extraction_outputs: List[str], assembly_player: str
) -> Task:
    """The terminal fan-in: gather every extraction finding into the final record.

    Depends on all extraction outputs. Its ``fields`` are *every* schema field —
    including the unresolved ones — so the record explicitly nulls what nothing
    could answer instead of leaving it to a heuristic.
    """
    inputs = {f"findings_{i}": name for i, name in enumerate(extraction_outputs)}
    return Task(
        task=_ASSEMBLY_TASK_NAME,
        player=assembly_player,
        rationale=(
            "Fan-in synthesis: one record from the per-extractor findings, keeping "
            "each field's value tied to the extractor the router routed it to."
        ),
        target_resources=[],
        fields=list(field_plan.routings.keys()),
        inputs=inputs,
        outputs=[_FINAL_ARTIFACT],
        topology="single",
    )