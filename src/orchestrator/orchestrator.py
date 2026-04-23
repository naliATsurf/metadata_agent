"""
Main Orchestrator - Coordinates planning and execution.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from langchain_core.output_parsers import PydanticOutputParser

from src.core.schemas import Plan, ExecutionResult

from ..config import DEFAULT_TOPOLOGY, LLM_PROVIDER, create_llm
from ..context import ContextType, ExecutionContext, create_context
from ..context.context_classifier import classify_context_type
from ..players import PLAYER_CONFIGS, Player, create_player_from_config
from ..tools.context_tools import (
    register_context,
    filter_tools_by_context_type,
)
from ..topology import EXECUTION_TOPOLOGIES
from .plan_executor import PlanExecutor
from .prompts import get_multi_csv_planning_prompt, get_single_csv_planning_prompt
from .utils import validate_plan_dataflow, validate_plan_tool_compatibility


class Orchestrator:
    """
    The main orchestrator that coordinates plan generation and execution.
    """
    MULTI_CONTEXT_TYPES = {
        ContextType.MULTI_CSV,
    }

    def __init__(
        self,
        topology_name: str,
        model_name: str,
        temperature: float,
        provider: str,
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

        dataflow_ok, dataflow_msg = validate_plan_dataflow(plan_dict)
        if not dataflow_ok:
            logging.error("Plan dataflow validation failed: %s", dataflow_msg)
            return False

        allowed_players = set(self._get_effective_player_pool(context))
        tools_ok, tools_msg = validate_plan_tool_compatibility(
            plan=plan_dict,
            context_type=context.context_type,
            allowed_players=allowed_players,
        )
        if not tools_ok:
            logging.error("Plan tool validation failed: %s", tools_msg)
            return False

        return True

    def _generate_player_manifest(self, context: ExecutionContext = None) -> str:
        player_pool = self._get_effective_player_pool(context)

        manifest_parts = []
        for role_name in player_pool:
            if role_name in PLAYER_CONFIGS:
                config = PLAYER_CONFIGS[role_name].copy()
                if context is not None:
                    config["tools"] = filter_tools_by_context_type(
                        config.get("tools", []), context.context_type
                    )
                player = create_player_from_config(config, name=role_name)
                manifest_parts.append(player.get_tool_manifest())

        return "\n\n".join(manifest_parts)

    def _generate_context_info(self, context: ExecutionContext) -> str:
        info_parts = [
            f"Context Name: {context.name}",
            f"Context Type: {context.context_type.value}",
            f"Multi-CSV: {context.is_multi_csv}",
            f"Resources: {', '.join(context.resources)}",
        ]

        if context.description:
            info_parts.insert(1, f"Description: {context.description}")

        info_parts.append("\nResource Details:")
        for resource in context.resources:
            try:
                resource_info = context.get_resource_info(resource)
                info_parts.append(
                    f"  - {resource}: {resource_info.item_count} items, "
                    f"{resource_info.field_count} fields ({', '.join(resource_info.field_names[:5])}{'...' if len(resource_info.field_names) > 5 else ''})"
                )
            except Exception:
                info_parts.append(f"  - {resource}: (info unavailable)")

        relationships = context.get_relationships()
        if relationships:
            info_parts.append("\nDiscovered Relationships:")
            for rel in relationships[:5]:
                info_parts.append(
                    f"  - {rel.from_resource}.{rel.from_field} -> "
                    f"{rel.to_resource}.{rel.to_field} ({rel.relationship_type})"
                )
            if len(relationships) > 5:
                info_parts.append(f"  ... and {len(relationships) - 5} more")

        return "\n".join(info_parts)

    def generate_plan(
        self, context: ExecutionContext, metadata_standard: str
    ) -> Optional[Plan]:
        classified_type = self._classify_context_for_planning(context)
        is_multi_context = classified_type in self.MULTI_CONTEXT_TYPES

        logging.info("=" * 60)
        logging.info("GENERATING PLAN")
        logging.info(f"Context: {context.name}")
        logging.info(f"Context type: {context.context_type.value}")
        logging.info(f"Classified planning type: {classified_type.value}")
        logging.info(f"Resources: {context.resources}")
        logging.info(f"Is multi-context: {is_multi_context}")
        logging.info("=" * 60)

        manifest = self._generate_player_manifest(context)
        context_info = self._generate_context_info(context)

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
            pass
    
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
