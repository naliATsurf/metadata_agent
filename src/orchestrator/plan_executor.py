"""
Plan Executor - Orchestrates the execution of a complete plan.

This module provides the PlanExecutor class that:
1. Takes a generated plan and execution topology
2. Iterates through each step sequentially
3. For each step, spawns parallel players and runs debates
4. Accumulates artifacts in a workspace
5. Produces the final metadata output

Uses the unified ExecutionContext abstraction for all data access.
"""
import logging
from typing import Dict, Any, List, Optional, Type

from pydantic import BaseModel

from src.core.schemas import Plan, ExecutionResult, StepResult
from src.context import ExecutionContext
from src.standards import get_schema_for_standard

from .step_executor import get_step_execution_graph, create_step_state
from ..topology import EXECUTION_TOPOLOGIES


class PlanExecutor:
    """
    Executes a complete plan using the specified topology.

    The executor iterates through each step in the plan, spawning
    parallel players and running debates as configured by the topology.

    Uses ExecutionContext for unified data access across all source types.
    """

    def __init__(self, topology_name: str = "default"):
        """
        Initialize the PlanExecutor with a topology.

        Args:
            topology_name: Name of the execution topology to use
        """
        if topology_name not in EXECUTION_TOPOLOGIES:
            available = list(EXECUTION_TOPOLOGIES.keys())
            raise ValueError(
                f"Unknown topology '{topology_name}'. Available: {available}"
            )

        self.topology_name = topology_name
        self.topology = EXECUTION_TOPOLOGIES[topology_name]
        self.step_graph = get_step_execution_graph()

        logging.info(f"PlanExecutor initialized with topology: {topology_name}")
        logging.info(f"  Players per step: {self.topology['players_per_step']}")
        logging.info(f"  Debate rounds: {self.topology['debate_rounds']}")
        logging.info(f"  Player pool: {self.topology['player_pool']}")

    def execute(
        self,
        plan: Plan,
        context: ExecutionContext,
        context_key: str,
        metadata_standard: str,
        metadata_standard_name: Optional[str] = None,
        player_pool: List[str] = None,
    ) -> ExecutionResult:
        """
        Execute the complete plan.

        Args:
            plan: The Plan object with steps to execute
            context: The ExecutionContext to analyze
            context_key: Key for the ExecutionContext in the tool registry
            metadata_standard: The metadata standard content (string template)
            metadata_standard_name: Optional name of the standard for schema lookup
            player_pool: Optional override for player pool (defaults to topology's pool)

        Returns:
            ExecutionResult with all step results and final metadata
        """
        effective_player_pool = player_pool or self.topology["player_pool"]
        
        # Try to get the Pydantic schema for structured output
        output_schema: Optional[Type[BaseModel]] = None
        if metadata_standard_name:
            output_schema = get_schema_for_standard(metadata_standard_name)
            if output_schema:
                logging.info(f"Using structured output schema: {output_schema.__name__}")
            else:
                logging.info(f"No schema found for '{metadata_standard_name}', using string output")

        logging.info("=" * 60)
        logging.info("STARTING PLAN EXECUTION")
        logging.info(f"Context: {context.name}")
        logging.info(f"Type: {context.context_type.value}")
        logging.info(f"Resources: {context.resources}")
        logging.info(f"Steps: {len(plan.steps)}")
        logging.info("=" * 60)

        # Initialize execution state
        workspace: Dict[str, Any] = {
            "_context_key": context_key,
            "_context_info": context.to_dict(),
            "metadata_standard": metadata_standard,  # Available as input for final synthesis step
        }
        step_results: List[StepResult] = []
        resource_metadata: Dict[str, Any] = {}

        # Pre-populate with schema info
        try:
            schema = context.get_schema()
            workspace["_schema"] = schema
        except Exception as e:
            logging.warning(f"Could not pre-load schema: {e}")

        # Convert plan steps to dict for processing
        plan_steps = plan.to_dict_list()

        # Execute each step
        for step_index, step_dict in enumerate(plan_steps):
            target_resources = step_dict.get("target_resources", [])

            logging.info("")
            logging.info(f"{'='*20} STEP {step_index + 1}/{len(plan_steps)} {'='*20}")
            logging.info(f"Task: {step_dict.get('task', 'Unknown')}")
            logging.info(f"Player: {step_dict.get('player', 'Unknown')}")
            logging.info(f"Rationale: {step_dict.get('rationale', 'None')}")
            if target_resources:
                logging.info(f"Target resources: {target_resources}")
            elif context.is_multi_csv:
                logging.info("Target resources: ALL (context-level)")

            try:
                # Determine if this is the final step (metadata_generator)
                # Pass the output schema only for the final step
                is_final_step = (step_index == len(plan_steps) - 1)
                is_metadata_generator = step_dict.get("player", "") == "metadata_generator"
                step_output_schema = output_schema if (is_final_step and is_metadata_generator) else None
                
                if step_output_schema:
                    logging.info(f"  Final step will use structured output: {step_output_schema.__name__}")
                
                # Create step state with ExecutionContext
                step_state = create_step_state(
                    step_index=step_index,
                    step_dict=step_dict,
                    context=context,
                    context_key=context_key,
                    workspace=workspace.copy(),
                    metadata_standard=metadata_standard,
                    players_per_step=self.topology["players_per_step"],
                    debate_rounds=self.topology["debate_rounds"],
                    player_pool=effective_player_pool,
                    output_schema=step_output_schema,
                )

                # Execute the step graph
                final_step_state = self.step_graph.invoke(step_state)

                # Check for errors
                if final_step_state.get("error"):
                    error_msg = final_step_state["error"]
                    logging.error(f"Step {step_index + 1} failed: {error_msg}")

                    step_results.append(
                        StepResult(
                            step_index=step_index,
                            task=step_dict.get("task", ""),
                            player_role=step_dict.get("player", ""),
                            individual_results=[],
                            debate_rounds_completed=final_step_state.get(
                                "current_debate_round", 0
                            ),
                            consolidated_result="",
                            artifacts={},
                            success=False,
                            error=error_msg,
                        )
                    )

                    # Continue to next step (or could choose to abort)
                    continue

                # Extract results
                produced_artifacts = final_step_state.get("produced_artifacts", {})
                consolidated_result = final_step_state.get("consolidated_result", "")

                # Update workspace with new artifacts
                workspace.update(produced_artifacts)

                # Collect per-resource metadata
                if context.is_multi_csv and target_resources:
                    for resource in target_resources:
                        if resource not in resource_metadata:
                            resource_metadata[resource] = {}
                        # Store resource-specific artifacts
                        for artifact_name, artifact_value in produced_artifacts.items():
                            if artifact_name.startswith(f"{resource}:"):
                                resource_metadata[resource][artifact_name] = (
                                    artifact_value
                                )

                # Record step result
                step_results.append(
                    StepResult(
                        step_index=step_index,
                        task=step_dict.get("task", ""),
                        player_role=step_dict.get("player", ""),
                        individual_results=final_step_state.get(
                            "player_results", []
                        ),
                        debate_rounds_completed=final_step_state.get(
                            "current_debate_round", 0
                        ),
                        consolidated_result=consolidated_result,
                        artifacts=produced_artifacts,
                        success=True,
                    )
                )

                logging.info(f"Step {step_index + 1} completed successfully")
                logging.info(
                    f"  Artifacts produced: {list(produced_artifacts.keys())}"
                )

            except Exception as e:
                error_msg = f"Unexpected error in step {step_index + 1}: {str(e)}"
                logging.error(error_msg)
                import traceback

                logging.error(traceback.format_exc())

                step_results.append(
                    StepResult(
                        step_index=step_index,
                        task=step_dict.get("task", ""),
                        player_role=step_dict.get("player", ""),
                        individual_results=[],
                        debate_rounds_completed=0,
                        consolidated_result="",
                        artifacts={},
                        success=False,
                        error=error_msg,
                    )
                )

        # Determine overall success
        successful_steps = sum(1 for r in step_results if r.success)
        overall_success = successful_steps == len(plan_steps)

        # Get relationships from context
        relationships = [r.to_dict() for r in context.get_relationships()]

        logging.info("")
        logging.info("=" * 60)
        logging.info("PLAN EXECUTION COMPLETE")
        logging.info(f"Steps completed: {successful_steps}/{len(plan_steps)}")
        logging.info(f"Overall success: {overall_success}")
        if context.is_multi_csv:
            logging.info(f"Resources: {context.resources}")
            logging.info(f"Relationships: {len(relationships)}")
        logging.info("=" * 60)

        return ExecutionResult(
            plan_steps_count=len(plan_steps),
            steps_completed=successful_steps,
            step_results=step_results,
            final_workspace=self._filter_workspace(workspace),
            final_metadata=self._extract_final_metadata(workspace, context),
            context_info=context.to_dict(),
            resource_metadata=resource_metadata,
            relationships=relationships,
            success=overall_success,
            error=None if overall_success else "Some steps failed",
        )

    def _filter_workspace(self, workspace: Dict[str, Any]) -> Dict[str, Any]:
        """Filter out internal workspace keys."""
        return {
            k: v for k, v in workspace.items()
            if not k.startswith("_")
        }

    def _extract_final_metadata(
        self,
        workspace: Dict[str, Any],
        context: ExecutionContext,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract the final metadata from the workspace.

        If the final step produced structured output (a dict from Pydantic),
        it will be in an artifact like 'final_metadata' or similar.
        This method looks for that and returns it directly if found.

        Args:
            workspace: The final workspace with all artifacts
            context: The ExecutionContext that was analyzed

        Returns:
            Structured metadata dictionary, or None if not found
        """
        filtered = self._filter_workspace(workspace)
        
        # Look for common final metadata artifact names
        # These are typically produced by the metadata_generator player
        final_artifact_names = [
            "final_metadata", "metadata", "generated_metadata", 
            "metadata_output", "final_output"
        ]
        
        for name in final_artifact_names:
            if name in filtered:
                value = filtered[name]
                # If it's already a dict (from structured output), return it
                if isinstance(value, dict):
                    logging.info(f"Found structured metadata in artifact '{name}'")
                    return value
        
        # Fallback: check if any artifact is a dict that looks like metadata
        # (has common metadata fields like 'title', 'description', etc.)
        for key, value in filtered.items():
            if isinstance(value, dict):
                # Check if it looks like metadata output
                if any(field in value for field in ['title', 'description', 'dataset', 'dataset_name']):
                    logging.info(f"Found metadata-like structure in artifact '{key}'")
                    return value
        
        # Legacy fallback: organize artifacts by resource
        if context.is_multi_csv:
            # Organize artifacts by resource
            resource_artifacts: Dict[str, Dict[str, Any]] = {}
            context_level: Dict[str, Any] = {}

            for key, value in filtered.items():
                if ":" in key:
                    resource, artifact = key.split(":", 1)
                    if resource not in resource_artifacts:
                        resource_artifacts[resource] = {}
                    resource_artifacts[resource][artifact] = value
                else:
                    context_level[key] = value

            return {
                "type": "multi_csv",
                "name": context.name,
                "context_type": context.context_type.value,
                "resources": resource_artifacts,
                "context_level": context_level,
                "artifact_count": len(filtered),
            }

        # Single-resource context fallback
        primary_resource = context.resources[0] if context.resources else None
        return {
            "type": "single_csv",
            "name": context.name,
            "context_type": context.context_type.value,
            "resource": primary_resource,
            "artifacts": filtered,
            "artifact_count": len(filtered),
        }


def execute_plan(
    plan: Plan,
    context: ExecutionContext,
    context_key: str,
    metadata_standard: str,
    metadata_standard_name: Optional[str] = None,
    topology_name: str = "default",
) -> ExecutionResult:
    """
    Convenience function to execute a plan.

    Args:
        plan: The Plan object to execute
        context: The ExecutionContext to analyze
        context_key: Key for the ExecutionContext in the tool registry
        metadata_standard: The metadata standard content (string template)
        metadata_standard_name: Optional name of the standard for schema lookup
        topology_name: Name of the execution topology

    Returns:
        ExecutionResult with all results
    """
    executor = PlanExecutor(topology_name=topology_name)
    return executor.execute(
        plan=plan,
        context=context,
        context_key=context_key,
        metadata_standard=metadata_standard,
        metadata_standard_name=metadata_standard_name,
    )
