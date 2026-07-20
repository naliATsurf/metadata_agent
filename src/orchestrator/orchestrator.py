"""
Main Orchestrator - Coordinates planning and execution.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from langchain_core.output_parsers import PydanticOutputParser

from src.core import ExecutionResult, Plan

from src.config import DEFAULT_TOPOLOGY, LLM_PROVIDER, PLANNING_TEMPERATURE, create_llm
from src.context import ContextType, ExecutionContext, create_context
from src.context.context_classifier import classify_context_type
from src.players import PLAYER_CONFIGS, Player, create_player_from_config
from src.tools.base import (
    register_context,
    survey_tools,
    tools_for,
    unregister_context,
)
from src.topology import EXECUTION_TOPOLOGIES
from src.orchestrator.plan_executor import PlanExecutor
from src.orchestrator.prompts import get_multi_csv_planning_prompt, get_single_csv_planning_prompt
from src.orchestrator.utils import (
    validate_plan_columns,
    validate_plan_dataflow,
    validate_plan_tool_compatibility,
)


class Orchestrator:
    """
    The main orchestrator that coordinates plan generation and execution.
    """
    MULTI_CONTEXT_TYPES = {
        ContextType.MULTI_CSV,
    }

    def __init__(
        self,
        topology_name: str = DEFAULT_TOPOLOGY,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        provider: Optional[str] = None,
    ):
        topology_name = topology_name or DEFAULT_TOPOLOGY
        temperature = temperature if temperature is not None else PLANNING_TEMPERATURE
        provider = provider or LLM_PROVIDER

        if topology_name not in EXECUTION_TOPOLOGIES:
            available = list(EXECUTION_TOPOLOGIES.keys())
            raise ValueError(
                f"Unknown topology '{topology_name}'. Available: {available}"
            )

        self.topology_name = topology_name
        self.topology = EXECUTION_TOPOLOGIES[topology_name]
        self.provider = provider

        self.llm = create_llm(
            model_name=model_name, temperature=temperature, provider=provider
        )
        self.parser = PydanticOutputParser(pydantic_object=Plan)

        self.executor = PlanExecutor(topology_name=topology_name)

        logging.info(f"Orchestrator initialized with topology: {topology_name}")

    def _classify_context_for_planning(self, context: ExecutionContext) -> ContextType:
        """
        Classify context for prompt routing using context classifier when possible.
        Falls back to context.context_type.
        """
        if hasattr(context, "get_all_file_paths"):
            try:
                file_paths = list(context.get_all_file_paths().values())
                return classify_context_type(file_paths)
            except Exception:
                pass
        return context.context_type

    def _get_planning_chain(self, classified_type: ContextType):
        """Return the planning chain based on classified context type."""
        if classified_type == ContextType.MULTI_CSV:
            prompt_template = get_multi_csv_planning_prompt()
        else:
            prompt_template = get_single_csv_planning_prompt()
        return prompt_template | self.llm | self.parser

    def _get_effective_player_pool(self, context: ExecutionContext = None) -> list:
        player_pool = list(self.topology.get("player_pool", []))

        # Always add metadata_generator for final step generation
        if "metadata_generator" not in player_pool:
            player_pool.append("metadata_generator")
            logging.info(
                "Auto-added 'metadata_generator' for final metadata generation"
            )

        if context:
            classified_type = self._classify_context_for_planning(context)
            is_multi_context = classified_type in self.MULTI_CONTEXT_TYPES
        else:
            is_multi_context = False

        if is_multi_context:
            if "relationship_analyst" not in player_pool:
                player_pool.append("relationship_analyst")
                logging.info(
                    "Auto-added 'relationship_analyst' for multi-context type"
                )

        return player_pool

    def _validate_plan(self, plan: Plan, context: ExecutionContext) -> bool:
        """
        Validate generated plan before execution.

        Keeps validation separate from plan generation so generate_plan stays pure.
        """
        plan_dict = plan.to_dict_list()

        columns_ok, columns_msg = validate_plan_columns(plan_dict, context)
        if not columns_ok:
            logging.error("Plan column validation failed: %s", columns_msg)
            return False

        dataflow_ok, dataflow_msg = validate_plan_dataflow(plan_dict)
        if not dataflow_ok:
            logging.error("Plan dataflow validation failed: %s", dataflow_msg)
            return False

        allowed_players = set(self._get_effective_player_pool(context))
        tools_ok, tools_msg = validate_plan_tool_compatibility(
            plan=plan_dict,
            context=context,
            allowed_players=allowed_players,
        )
        if not tools_ok:
            logging.error("Plan tool validation failed: %s", tools_msg)
            return False

        return True

    def _generate_player_manifest(self, context: ExecutionContext) -> str:
        player_pool = self._get_effective_player_pool(context)

        manifest_parts = []
        for role_name in player_pool:
            if role_name in PLAYER_CONFIGS:
                player = create_player_from_config(
                    PLAYER_CONFIGS[role_name],
                    name=role_name,
                    context=context,
                    role_key=role_name,
                )
                manifest_parts.append(player.get_tool_manifest())

        return "\n\n".join(manifest_parts)

    def _generate_context_info(self, context: ExecutionContext) -> str:
        info_parts = [
            f"Context Name: {context.name}",
            f"Context Type: {context.context_type.value}",
            f"Resource Count: {len(context.resources)}",
            f"Resources: {', '.join(context.resources)}",
        ]

        if context.description:
            info_parts.insert(1, f"Description: {context.description}")

        info_parts.append("\nResource Details:")
        for resource in context.resources:
            try:
                resource_info = context.get_resource_info(resource)
                info_parts.append(f"  - {resource}: {resource_info.summary()}")
            except Exception:
                info_parts.append(f"  - {resource}: (info unavailable)")

        relationships = context.get_relationships()
        if relationships:
            info_parts.append("\nDiscovered Relationships:")
            for rel in relationships[:5]:
                info_parts.append(f"  - {rel.describe()}")
            if len(relationships) > 5:
                info_parts.append(f"  ... and {len(relationships) - 5} more")

        return "\n".join(info_parts)

    def _inspect_context(self, context: ExecutionContext) -> str:
        """Deterministically inspect the data so the planner sees content, not just names.

        The "inspect" in inspect-then-plan. Runs the same auto-fireable tool sweep
        the player survey uses (:func:`~src.tools.base.survey_tools`) over whatever
        tools the context serves, and hands the raw results to the planner. It is
        standard-agnostic: it reports *whatever the available tools find* — field
        stats, detected columns, samples — not any one standard's notion of what
        matters. No LLM involved.
        """
        key = f"inspect_{uuid.uuid4().hex[:8]}"
        register_context(key, context)
        try:
            findings = survey_tools(key, tools_for(context), context.resources)
            if not findings:
                return "Data profile: no inspection tools available for this context."
            lines = []
            for name, value in findings.items():
                rendered = str(value)
                if len(rendered) > 300:
                    rendered = rendered[:300] + " …(truncated)"
                lines.append(f"- {name}: {rendered}")
            return (
                "Data profile (from inspecting the actual data with the available "
                "tools):\n" + "\n".join(lines)
            )
        finally:
            unregister_context(key)

    def generate_plan(
        self, context: ExecutionContext, metadata_standard: str
    ) -> Optional[Plan]:
        """Generate an executable metadata extraction plan for a context.

        Classifies the context, builds the player manifest and context summary,
        invokes the appropriate planning chain, and returns the parsed plan.
        Returns ``None`` if the planner fails or produces an invalid response.

        Args:
            context: Execution context containing the available resources.
            metadata_standard: Metadata standard the generated plan should target.

        Returns:
            A generated plan when planning succeeds; otherwise ``None``.
        """
        classified_type = self._classify_context_for_planning(context)
        is_multi_context = classified_type in self.MULTI_CONTEXT_TYPES

        logging.info("[ui] planning")
        logging.info("=" * 60)
        logging.info("GENERATING PLAN")
        logging.info(f"Classified planning type: {classified_type.value}")
        logging.info(f"Is multi-context: {is_multi_context}")

        manifest = self._generate_player_manifest(context)
        context_info = self._generate_context_info(context)
        # Inspect-then-plan: ground the plan in what the data actually contains.
        context_info += "\n\n" + self._inspect_context(context)

        logging.info("Prepared planning inputs.")
        logging.info("  Context summary length: %d chars", len(context_info))
        logging.info("  Player manifest length: %d chars", len(manifest))
        logging.info("  Resource count: %d", len(context.resources))

        try:
            format_instructions = self.parser.get_format_instructions()
            planning_chain = self._get_planning_chain(classified_type)

            if is_multi_context:
                prompt_inputs = {
                    "dataset_info": context_info,
                    "dataset_name": context.name,
                    "table_names": ", ".join(context.resources),
                    "file_type": context.context_type.value.upper(),
                    "available_players": manifest,
                    "metadata_standard": metadata_standard,
                    "format_instructions": format_instructions,
                }

                generated_plan = planning_chain.invoke(prompt_inputs)
            else:
                prompt_inputs = {
                    "dataset_info": context_info,
                    "file_type": context.context_type.value.upper(),
                    "available_players": manifest,
                    "metadata_standard": metadata_standard,
                    "format_instructions": format_instructions,
                }

                generated_plan = planning_chain.invoke(prompt_inputs)

            logging.info("Plan generated successfully!")
            logging.info(f"Number of steps: {len(generated_plan.steps)}")
            for i, step in enumerate(generated_plan.steps):
                target_info = (
                    f" (resources: {step.target_resources})" if step.target_resources else ""
                )
                logging.info(
                    f"  Step {i + 1}: {step.task} (player: {step.player}){target_info}"
                )

            return generated_plan

        except Exception as e:
            logging.error(f"Plan generation failed: {e}")

            try:
                if is_multi_context:
                    raw_prompt = get_multi_csv_planning_prompt()
                else:
                    raw_prompt = get_single_csv_planning_prompt()
                raw_output = (raw_prompt | self.llm).invoke(
                    {
                        k: v
                        for k, v in prompt_inputs.items()
                        if k != "format_instructions"
                    }
                )
                logging.error(f"Raw LLM output: {raw_output}")
            except Exception:
                pass

            return None

    def execute_plan(
        self, 
        plan: Plan, 
        context: ExecutionContext, 
        metadata_standard: str,
        metadata_standard_name: Optional[str] = None,
    ) -> ExecutionResult:
        """
        A wrapper around the PlanExecutor to run a plan with the given context and metadata standard. 

        Args:
            plan: The plan to execute.
            context: Execution context containing source data and derived state.
            metadata_standard: Metadata standard content used by executor steps.
            metadata_standard_name: Optional standard name for structured output.

        Returns:
            ExecutionResult produced by the plan executor.
        """
        # Validate before executing, so a plan is never run just because a caller
        # reached execute_plan directly (bypassing run()). Returns a failed result
        # rather than raising, keeping the ExecutionResult contract.
        if not self._validate_plan(plan, context):
            logging.error("Plan failed validation; aborting execution.")
            return ExecutionResult(
                plan_steps_count=len(plan.steps),
                steps_completed=0,
                success=False,
                error="Plan failed validation before execution (see logs).",
            )

        context_key = f"ctx_{uuid.uuid4().hex[:8]}"
        register_context(context_key, context)

        effective_player_pool = self._get_effective_player_pool(context)

        try:
            return self.executor.execute(
                plan=plan,
                context=context,
                context_key=context_key,
                metadata_standard=metadata_standard,
                metadata_standard_name=metadata_standard_name,
                player_pool=effective_player_pool,
            )
        finally:
            # Per-run teardown. executor.execute has already serialized the
            # evidence into ExecutionResult.final_evidence by the time it returns,
            # so dropping it from the global ledger here is safe and keeps the
            # registries from growing across runs.
            unregister_context(context_key)
    
    # decorators for logging?
    def run(
        self,
        source: Union[str, List[str], Dict[str, str], ExecutionContext],
        metadata_standard: str,
        metadata_standard_name: Optional[str] = None,
        name: str = "context",
        **kwargs,
    ) -> Optional[ExecutionResult]:
        """
        Run the complete orchestration: generate plan and execute.
        
        Args:
            source: Data source (path, list of paths, dict, or ExecutionContext)
            metadata_standard: The metadata standard content (string template)
            metadata_standard_name: Optional name of the standard (e.g., "dublin_core", 
                                   "relational") for structured output. If provided,
                                   the final step will use Pydantic validation.
            name: Name for the context
            **kwargs: Additional arguments passed to create_context
            
        Returns:
            ExecutionResult with final metadata as structured dict
        """
        if isinstance(source, ExecutionContext):
            context = source
        else:
            context = create_context(source, name=name, **kwargs)

        logging.info("=" * 60)
        logging.info("STARTING ORCHESTRATION")
        logging.info(f"Context: {context.name}")
        logging.info(f"Type: {context.context_type.value}")
        logging.info(f"Resources: {context.resources}")
        if metadata_standard_name:
            logging.info(f"Metadata standard: {metadata_standard_name} (structured output)")
        logging.info("=" * 60)

        plan = self.generate_plan(context=context, metadata_standard=metadata_standard)

        if plan is None:
            logging.error("Failed to generate plan. Aborting execution.")
            return None

        if not self._validate_plan(plan, context):
            logging.error("Generated plan failed validation. Aborting execution.")
            return None

        result = self.execute_plan(
            plan=plan, 
            context=context, 
            metadata_standard=metadata_standard,
            metadata_standard_name=metadata_standard_name,
        )

        return result


def run_metadata_extraction(
    source: Union[str, List[str], Dict[str, str], ExecutionContext],
    metadata_standard: str,
    metadata_standard_name: Optional[str] = None,
    name: str = "context",
    topology_name: str = "default",
    **kwargs,
) -> Optional[ExecutionResult]:
    """
    Convenience function to run metadata extraction.
    
    Args:
        source: Data source (path, list of paths, dict, or ExecutionContext)
        metadata_standard: The metadata standard content (string template)
        metadata_standard_name: Optional name of the standard for structured output
        name: Name for the context
        topology_name: Name of the execution topology
        **kwargs: Additional arguments passed to create_context
        
    Returns:
        ExecutionResult with final metadata
    """
    orchestrator = Orchestrator(topology_name=topology_name)
    return orchestrator.run(
        source=source, 
        metadata_standard=metadata_standard, 
        metadata_standard_name=metadata_standard_name,
        name=name, 
        **kwargs
    )
