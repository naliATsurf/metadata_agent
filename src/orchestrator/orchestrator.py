"""
Main Orchestrator - Coordinates planning and execution.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from langchain_core.output_parsers import PydanticOutputParser

from src.core.schemas import Plan, ExecutionResult

from ..config import DEFAULT_TOPOLOGY, LLM_PROVIDER, PLANNING_TEMPERATURE, create_llm
from ..context import ExecutionContext, create_context
from ..players import PLAYER_CONFIGS, Player, create_player_from_config
from ..tools.context_tools import clear_registry, register_context
from ..topology import EXECUTION_TOPOLOGIES
from .plan_executor import PlanExecutor
from .prompts import get_multi_table_planning_prompt, get_planning_prompt


class Orchestrator:
    """
    The main orchestrator that coordinates plan generation and execution.
    """

    def __init__(
        self,
        topology_name: str = None,
        model_name: str = None,
        temperature: float = None,
        provider: str = None,
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
        self.prompt_template = get_planning_prompt()
        self.planning_chain = self.prompt_template | self.llm | self.parser

        self.executor = PlanExecutor(topology_name=topology_name)

        logging.info(f"Orchestrator initialized with topology: {topology_name}")

    def _get_effective_player_pool(self, context: ExecutionContext = None) -> list:
        player_pool = list(self.topology.get("player_pool", []))

        # Always add metadata_generator for final step generation
        if "metadata_generator" not in player_pool:
            player_pool.append("metadata_generator")
            logging.info(
                "Auto-added 'metadata_generator' for final metadata generation"
            )

        if context and context.is_multi_resource:
            if "relationship_analyst" not in player_pool:
                player_pool.append("relationship_analyst")
                logging.info(
                    "Auto-added 'relationship_analyst' for multi-resource context"
                )

        return player_pool

    def _generate_player_manifest(self, context: ExecutionContext = None) -> str:
        player_pool = self._get_effective_player_pool(context)

        manifest_parts = []
        for role_name in player_pool:
            if role_name in PLAYER_CONFIGS:
                config = PLAYER_CONFIGS[role_name]
                player = create_player_from_config(config, name=role_name)
                manifest_parts.append(player.get_tool_manifest())

        return "\n\n".join(manifest_parts)

    def _generate_context_info(self, context: ExecutionContext) -> str:
        info_parts = [
            f"Context Name: {context.name}",
            f"Context Type: {context.context_type.value}",
            f"Multi-resource: {context.is_multi_resource}",
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
        is_multi_resource = context.is_multi_resource

        logging.info("=" * 60)
        logging.info("GENERATING PLAN")
        logging.info(f"Context: {context.name}")
        logging.info(f"Context type: {context.context_type.value}")
        logging.info(f"Resources: {context.resources}")
        logging.info(f"Multi-resource: {is_multi_resource}")
        logging.info("=" * 60)

        manifest = self._generate_player_manifest(context)
        context_info = self._generate_context_info(context)

        logging.info("Context info:")
        logging.info(context_info)
        logging.info("-" * 40)
        logging.info("Available players manifest")
        logging.info(manifest)
        logging.info("-" * 40)

        try:
            format_instructions = self.parser.get_format_instructions()

            if is_multi_resource:
                multi_resource_prompt = get_multi_table_planning_prompt()
                multi_resource_chain = multi_resource_prompt | self.llm | self.parser

                prompt_inputs = {
                    "dataset_info": context_info,
                    "dataset_name": context.name,
                    "table_names": ", ".join(context.resources),
                    "file_type": context.context_type.value.upper(),
                    "available_players": manifest,
                    "metadata_standard": metadata_standard,
                    "format_instructions": format_instructions,
                }

                generated_plan = multi_resource_chain.invoke(prompt_inputs)
            else:
                prompt_inputs = {
                    "file_type": context.context_type.value.upper(),
                    "available_players": manifest,
                    "metadata_standard": metadata_standard,
                    "format_instructions": format_instructions,
                }

                generated_plan = self.planning_chain.invoke(prompt_inputs)

            logging.info("Plan generated successfully!")
            logging.info(f"Number of steps: {len(generated_plan.steps)}")
            for i, step in enumerate(generated_plan.steps):
                target_info = (
                    f" (resources: {step.target_tables})" if step.target_tables else ""
                )
                logging.info(
                    f"  Step {i + 1}: {step.task} (player: {step.player}){target_info}"
                )

            return generated_plan

        except Exception as e:
            logging.error(f"Plan generation failed: {e}")

            try:
                if is_multi_resource:
                    raw_output = (multi_resource_prompt | self.llm).invoke(
                        {
                            k: v
                            for k, v in prompt_inputs.items()
                            if k != "format_instructions"
                        }
                    )
                else:
                    raw_output = (self.prompt_template | self.llm).invoke(
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
