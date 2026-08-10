"""
Core Pydantic schemas for the multi-agent system.

This module defines the fundamental data structures:

1. Task: A single, executable unit of work.
2. Plan: A sequence of tasks with optional validation for data flow.
3. StepResult / ExecutionResult: Standard execution outputs for orchestrations.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, validator

from src.core.constants import DEFAULT_WORKSPACE_ARTIFACTS


class Task(BaseModel):
    """
    A single, executable step in the agent's plan.

    Attributes:
        task: A specific and single task to be performed, e.g., 'get_row_count'.
        player: The name of the player role responsible for executing this task, e.g., 'data_analyst'.
        rationale: The reasoning for why this step is necessary.
        target_resources: List of resource names this step should operate on. Empty means all resources or context-level operation.
        inputs: Maps a task's parameter names to the names of artifacts in the workspace that should be used as input.
        outputs: A list of new artifact names that this step will produce and save to the workspace.
    """

    task: str = Field(
        description="A specific and single task to be performed, e.g., 'get_row_count'."
    )
    player: str = Field(
        description="The name of the player role responsible for executing this task, e.g., 'data_analyst'."
    )
    rationale: str = Field(description="The reasoning for why this step is necessary.")
    target_resources: List[str] = Field(
        default_factory=list,
        description="List of resource names this step should operate on. Empty means all resources or context-level operation.",
    )
    inputs: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps a task's parameter names to the names of artifacts in the workspace that should be used as input.",
    )
    outputs: List[str] = Field(
        default_factory=list,
        description="A list of new artifact names that this step will produce and save to the workspace.",
    )

    # --- Field-driven routing (layer 5). Additive and optional: the source-driven
    # LLM planner leaves these empty and the executor ignores them, so both planners
    # emit the same Task shape. The field-driven compiler populates them. ---
    fields: List[str] = Field(
        default_factory=list,
        description=(
            "The metadata standard field paths this task is responsible for filling "
            "(field-driven routing). Carries field identity from the FieldPlan into "
            "execution so a produced value maps back to the field it answers, rather "
            "than being re-matched by heuristics afterwards."
        ),
    )
    candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "The deduped union of this task's candidate evidence (serialized "
            "EvidenceRefs) — a compact overview of the curated context slice its "
            "fields draw on. The authoritative per-field binding is `field_bindings`."
        ),
    )
    field_bindings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-field routing bindings (field-driven routing). Each entry is "
            "{field, query, assurance, candidates}, where `candidates` is the "
            "router's *ranked* list for that one field — a proposed set, not a "
            "committed pick. The executor selects which candidate actually answers "
            "the field and extracts from it (or reports none); the router proposes, "
            "the evidence disposes. `assurance` is provisional — the router's grade, "
            "confirmed or revised by the verify pass. This is what carries a field's "
            "candidate set structurally, so a value can be traced to its source "
            "without parsing the instruction text."
        ),
    )
    topology: Optional[str] = Field(
        default=None,
        description=(
            "Per-task execution topology hint chosen from the fields' assurance: "
            "'single' for high-assurance computed fields, 'debate' for contested or "
            "low-assurance ones. A hint the executor may honor; None defers to the "
            "run-level topology."
        ),
    )


class Plan(BaseModel):
    """
    The complete, multi-step plan for the agent to execute.
    Includes validation to ensure logical consistency of task dependencies.

    Attributes:
        steps: The list of sequential tasks the agent should follow.
    """

    steps: List[Task] = Field(
        description="The list of sequential tasks the agent should follow."
    )

    @validator("steps")
    def validate_task_dependencies(cls, steps: List[Task]) -> List[Task]:
        """
        Best-effort validation that the inputs for each task are produced by a previous task.

        IMPORTANT:

        - This validator is **non-fatal**: it logs a warning on unmet dependencies
          but does NOT raise, to avoid hard failures on imperfect LLM plans.
        """
        produced_artifacts: Set[str] = set(DEFAULT_WORKSPACE_ARTIFACTS)

        for i, step in enumerate(steps):
            # Get the names of the artifacts required by the current step
            required_artifacts = set(step.inputs.values())

            # Check if all required artifacts have been produced by previous steps
            if not required_artifacts.issubset(produced_artifacts):
                missing = required_artifacts - produced_artifacts
                logging.warning(
                    "Plan validation warning: Step %d ('%s') has unmet dependencies. "
                    "Missing artifacts: %s",
                    i,
                    step.task,
                    missing,
                )

            # Add the artifacts produced by the current step to the set of available artifacts
            for output_artifact in step.outputs:
                produced_artifacts.add(output_artifact)

        return steps

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """Convert plan steps to a list of dictionaries for state management."""
        return [step.model_dump() for step in self.steps]

    def pretty_print(self):
        print("Plan Steps:")
        for i, step in enumerate(self.steps):
            print(f"Step {i}: {step.task}")
            print(f"  Rationale: {step.rationale}")
            print(f"  Required Artifacts: {step.inputs}")
            print(f"  Produced Artifacts: {step.outputs}")


class StepResult(BaseModel):
    """
    The result of executing a single plan step.

    Attributes:
        step_index: The index of the step in the plan.
        task: The task that was executed.
        player_role: The player role that executed this step.
        individual_results: Results from each player that worked on this step.
        debate_rounds_completed: Number of debate rounds that were completed.
        consolidated_result: The synthesized result after debate (string or Pydantic model).
        artifacts: Artifacts produced by this step to add to workspace.
        success: Whether the step completed successfully.
        error: Error message if the step failed.
    """

    step_index: int = Field(description="The index of the step in the plan.")
    task: str = Field(description="The task that was executed.")
    player_role: str = Field(description="The player role that executed this step.")

    individual_results: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Results from each player that worked on this step.",
    )
    debate_rounds_completed: int = Field(
        default=0, description="Number of debate rounds that were completed."
    )
    consolidated_result: Any = Field(
        default="", description="The synthesized result after debate (string or Pydantic model)."
    )
    artifacts: Dict[str, Any] = Field(
        default_factory=dict,
        description="Artifacts produced by this step to add to workspace.",
    )
    success: bool = Field(
        default=True, description="Whether the step completed successfully."
    )
    error: Optional[str] = Field(
        default=None, description="Error message if the step failed."
    )


class ExecutionResult(BaseModel):
    """
    The complete result of executing a plan.

    Attributes:
        plan_steps_count: Total number of steps in the plan.
        steps_completed: Number of steps successfully completed.
        step_results: Results from each step.
        final_workspace: Final state of the workspace with all artifacts.
        final_metadata: The final extracted metadata.
        final_provenance: Per-field provenance, parallel to final_metadata.
        final_evidence: Every fact the tools produced during the run — the raw
            material the provenance sidecar cites by evidence_id.
        context_info: Information about the ExecutionContext used.
        resource_metadata: Per-resource metadata, keyed by resource name.
        relationships: Relationships between resources (from ExecutionContext).
        success: Whether the entire plan executed successfully.
        error: Error message if execution failed.
    """

    plan_steps_count: int = Field(description="Total number of steps in the plan.")
    steps_completed: int = Field(description="Number of steps successfully completed.")

    step_results: List[StepResult] = Field(
        default_factory=list, description="Results from each step."
    )
    final_workspace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Final state of the workspace with all artifacts.",
    )
    final_metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="The final extracted metadata."
    )
    final_provenance: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Per-field provenance, parallel to final_metadata: for each field, "
            "the tool call whose captured output supports the value (or an "
            "unverifiable/not_present status when nothing does)."
        ),
    )
    final_evidence: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Every tool invocation captured during the run, in order: the fact "
            "each tool produced, addressable by evidence_id. The raw material the "
            "provenance sidecar attributes values to, and what makes an "
            "'unverifiable' field interpretable — you can see what was available. "
            "Each entry's 'used_by' lists the callers (agent/role/step/phase) that "
            "produced or reused the fact, so a deduplicated fact still shows who "
            "asked and at which step."
        ),
    )

    context_info: Dict[str, Any] = Field(
        default_factory=dict, description="Information about the ExecutionContext used."
    )
    resource_metadata: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-resource metadata, keyed by resource name.",
    )
    relationships: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Relationships between resources (from ExecutionContext).",
    )

    success: bool = Field(
        default=True, description="Whether the entire plan executed successfully."
    )
    error: Optional[str] = Field(
        default=None, description="Error message if execution failed."
    )
