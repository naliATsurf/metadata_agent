"""
Unified Player class for the multi-agent system.

A ``Player`` is a self-contained agent that can:
    1. Execute tasks using tools.
    2. Participate in debates by generating work, critiquing, and revising.
    3. Synthesize results from multiple sources.

Each player has a role/persona defined by a prompt, and a set of tools
it can use to accomplish tasks.

Uses the unified :class:`~src.context.ExecutionContext` abstraction for all data
access.
"""
import logging
from typing import List, Dict, Any, Optional, Union, Type

from pydantic import BaseModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.config import (
    LLM_PROVIDER,
    PLAYER_MAX_TOOL_ITERATIONS,
    PLAYER_TEMPERATURE,
    PLAYER_TOOL_EXECUTION_MODE,
    create_llm,
)
from src.context.base_context import ExecutionContext
from src.provenance import Caller, attributed_to
from src.tools.base import (
    is_auto_fireable,
    resolve_toolsets,
    survey_tools,
)


def _format_args(args: Dict[str, Any]) -> str:
    """Render tool-call arguments for use as a result key."""
    return ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "context_key")


def _describe_context(
    context_info: Dict[str, Any], target_resources: List[str], context_key: str
) -> str:
    """Build the context preamble shown to the player."""
    resources = context_info.get("resources", [])
    lines = [
        f"Context: {context_info.get('name', 'context')}",
        f"Context type: {context_info.get('context_type', 'unknown')}",
    ]

    if context_info.get("is_multi_resource", False):
        lines.append(f"Resources: {', '.join(resources)}")
        if target_resources:
            lines.append(
                f"Target resources for this step: {', '.join(target_resources)}"
            )
    else:
        lines.append(f"Resource: {resources[0] if resources else 'unknown'}")

    lines.append(f"\nTo use tools, pass context_key='{context_key}'")
    return "\n".join(lines)


class Player:
    """
    A unified player agent capable of executing tasks and participating in debates.
    
    Attributes:
        name: Unique identifier for this player instance
        role_prompt: The persona/role description that guides the player's behavior
        tools: List of tools available to this player
        llm: The language model instance for this player
    """
    
    def __init__(
        self,
        name: str,
        role_prompt: str,
        tools: Optional[List[BaseTool]] = None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        provider: Optional[str] = None,
        role_key: Optional[str] = None,
    ):
        """
        Initialize a Player with a role and tools.
        
        Args:
            name: Unique identifier for this player
            role_prompt: Description of the player's role/persona
            tools: List of LangChain tools available to this player
            model_name: The LLM model to use (default from config)
            temperature: LLM temperature (default from config)
            provider: LLM provider to use (default from config)
            role_key: Canonical player role key from PLAYER_CONFIGS
        """
        # Use config defaults if not specified
        temperature = temperature if temperature is not None else PLAYER_TEMPERATURE
        provider = provider or LLM_PROVIDER
        
        self.name = name
        self.role_key = role_key or name
        self.role_prompt = role_prompt
        self.tools = tools or []
        self.llm = create_llm(
            model_name=model_name,
            temperature=temperature,
            provider=provider
        )
        self._output_parser = StrOutputParser()
    
    def get_tool_manifest(self) -> str:
        """
        Generates a string manifest of the tools available to this player.
        Used by the orchestrator for planning.
        """
        if not self.tools:
            return f"Player: {self.name}\n  Description: {self.role_prompt}\n  Tools: None"
        
        manifest = f"Player: {self.name}\n"
        manifest += f"  Description: {self.role_prompt}\n"
        tasks = [f"{tool.name}: {tool.description}" for tool in self.tools]
        manifest += f"  Tools:\n" + "\n".join([f"    - {task}" for task in tasks])
        return manifest
    
    def _survey(
        self,
        context_key: str,
        resources: List[str],
    ) -> Dict[str, Any]:
        """Run every auto-fireable tool this player owns over the context.

        Deterministic and cheap: the evidence bundle the player always gathers,
        before the model chooses any parameterized tools. Delegates to the shared
        :func:`~src.tools.base.survey_tools` so the same sweep backs both this
        phase and the orchestrator's inspect-then-plan pass.
        """
        return survey_tools(context_key, self.tools, resources)

    def _investigate(
        self,
        task: str,
        context_key: str,
        survey: Dict[str, Any],
        tools: List[BaseTool],
    ) -> Dict[str, Any]:
        """Let the model call the tools whose arguments only it can supply.

        Tools needing a column or field name cannot be fired blindly. The survey
        is passed in as the seed, so the model already knows which columns exist
        before choosing what to analyze.
        """
        try:
            llm_with_tools = self.llm.bind_tools(tools)
        except (AttributeError, NotImplementedError) as e:
            logging.warning(
                "Provider does not support tool calling (%s); "
                "skipping investigation phase for '%s'.", e, self.name
            )
            return {}

        by_name = {tool.name: tool for tool in tools}
        messages: List[Any] = [
            SystemMessage(
                content=(
                    f"You are {self.name}. {self.role_prompt}\n\n"
                    f"Call the available tools to investigate the task. Always pass "
                    f"context_key='{context_key}'. Use the survey below to choose "
                    f"concrete column and field names — never guess them. Stop "
                    f"calling tools once you have what the task needs."
                )
            ),
            HumanMessage(
                content=f"Task: {task}\n\nSurvey of the context:\n{survey}"
            ),
        ]

        results: Dict[str, Any] = {}
        for _ in range(PLAYER_MAX_TOOL_ITERATIONS):
            response = llm_with_tools.invoke(messages)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                break

            for call in tool_calls:
                tool = by_name.get(call["name"])
                if tool is None:
                    output = f"Error: unknown tool '{call['name']}'"
                else:
                    # The key is ours to supply, never the model's to invent.
                    args = {**call["args"], "context_key": context_key}
                    try:
                        output = tool.invoke(args)
                    except Exception as e:
                        output = f"Error: {e}"

                results[f"{call['name']}({_format_args(call['args'])})"] = output
                messages.append(
                    ToolMessage(content=str(output), tool_call_id=call["id"])
                )

        return results

    def execute_task(
        self,
        task: str,
        context_key: str,
        context_info: Dict[str, Any],
        workspace: Dict[str, Any],
        inputs: Dict[str, str],
        target_resources: Optional[List[str]] = None,
        step_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Execute a specific task using available tools.

        Tools are gathered in two phases. The *survey* fires every tool whose
        arguments the runner can supply on its own. The *investigation* then
        offers the remaining tools — the ones needing a column or field name —
        to the model, seeded with the survey so it can choose real arguments.
        The player finally reasons over the combined evidence.

        Args:
            task: The task description to execute
            context_key: Key for the ExecutionContext in the tool registry
            context_info: Serialized info about the ExecutionContext
            workspace: Dictionary of artifacts from previous steps
            inputs: Mapping of parameter names to artifact names in workspace
            target_resources: List of specific resources this task targets
            step_index: Index of this step in the plan, recorded on every fact
                this player's tool calls produce or reuse (provenance attribution)

        Returns:
            Dictionary containing the execution result and any produced artifacts
        """
        resolved_inputs = {
            param_name: workspace.get(artifact_name, f"[MISSING: {artifact_name}]")
            for param_name, artifact_name in inputs.items()
        }

        is_multi_resource = context_info.get("is_multi_resource", False)
        resources = context_info.get("resources", [])
        target_resources = target_resources or []
        resources_to_analyze = target_resources or resources

        # Attribute every fact this step's tools produce or reuse to this player,
        # so the evidence ledger records who asked and at which step. The survey
        # and investigation are distinct phases and tagged as such.
        def _as(phase: str) -> Caller:
            return Caller(
                agent="player", role=self.role_key, step=step_index, phase=phase
            )

        with attributed_to(_as("survey")):
            tool_results = self._survey(context_key, resources_to_analyze)

        investigable = [t for t in self.tools if not is_auto_fireable(t)]
        if investigable and PLAYER_TOOL_EXECUTION_MODE == "investigate":
            with attributed_to(_as("investigate")):
                tool_results.update(
                    self._investigate(task, context_key, tool_results, investigable)
                )

        ctx_info = _describe_context(context_info, target_resources, context_key)
        tool_descriptions = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in self.tools
        ) or "No tools available."

        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are {self.name}. {self.role_prompt}

The following tools were run on your behalf; their results are provided below.
{tool_descriptions}

Base every factual claim on those results. Do not invent values.

{ctx_info}

For multi-resource contexts, consider:
- How resources might relate to each other
- Common fields that could be foreign keys
- Data integrity across resources
"""),
            ("human", """Task: {task}

Target resources for this step: {target_resources}

Input context from previous steps:
{input_context}

Execute this task and provide a comprehensive response. Include:
1. Your approach to the task
2. Any relevant observations or findings
3. The result of your analysis""")
        ])

        input_context = "\n".join(
            f"- {k}: {v}" for k, v in resolved_inputs.items()
        ) or "No inputs from previous steps."

        chain = prompt | self.llm | self._output_parser
        llm_response = chain.invoke({
            "task": task,
            "target_resources": ", ".join(target_resources) if target_resources else (
                "All resources" if is_multi_resource else "N/A"
            ),
            "input_context": input_context + "\n\nTool Results:\n" + str(tool_results),
        })

        return {
            "player": self.name,
            "task": task,
            "tool_results": tool_results,
            "analysis": llm_response,
            "success": True,
            "is_multi_resource": is_multi_resource,
        }
    
    def generate_initial_work(
        self,
        task: str,
        context_info: Dict[str, Any],
        context: Dict[str, Any]
    ) -> str:
        """
        Generate initial work/analysis for a debate round.

        Prompt:
            System: You are {self.name}. {self.role_prompt}

            You are participating in a multi-agent analysis of a context
            (dataset, API, etc.). Your goal is to provide your unique
            perspective and insights.

            Human:
                Task: {task}

                Context: {context_name} ({context_type})
                Resources: {resources}

                Context and available information:
                {context}

                Provide your initial analysis. Be thorough and specific.
                Focus on what you can contribute based on your role.
        
        Args:
            task: The task to work on
            context_info: Info about the ExecutionContext
            context: Additional context (workspace, tool results, etc.)
            
        Returns:
            The player's initial analysis as a string
        """
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    f"""You are {self.name}. {self.role_prompt}

You are participating in a multi-agent analysis of a context (dataset, API, etc.).
Your goal is to provide your unique perspective and insights.""",
                ),
                (
                    "human",
                    """Task: {task}

Context: {context_name} ({context_type})
Resources: {resources}

Context and available information:
{context}

Provide your initial analysis. Be thorough and specific.
Focus on what you can contribute based on your role.""",
                ),
            ]
        )
        
        chain = prompt | self.llm | self._output_parser
        
        return chain.invoke({
            "task": task,
            "context_name": context_info.get("name", "context"),
            "context_type": context_info.get("context_type", "unknown"),
            "resources": ", ".join(context_info.get("resources", [])),
            "context": str(context),
        })
    def critique_work(
        self,
        task: str,
        other_players_work: Dict[str, str]
    ) -> str:
        """
        Critique the work of other players.

        Prompt:
            System:
                You are {self.name}. {self.role_prompt}

                You are reviewing the work of other analysts. Provide constructive criticism
                that helps improve the overall analysis. Be specific about what could be
                improved, what's missing, or what might be incorrect.

            Human:
                Task: {task}

                Work from other players to critique:
                {other_work}

                Provide your critique. Focus on:
                1. Accuracy and correctness
                2. Completeness
                3. Clarity and specificity
                4. Suggestions for improvement
        
        Args:
            task: The task being worked on
            other_players_work: Dictionary mapping player names to their work
            
        Returns:
            Critique as a string
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are {self.name}. {self.role_prompt}

You are reviewing the work of other analysts. Provide constructive criticism
that helps improve the overall analysis. Be specific about what could be
improved, what's missing, or what might be incorrect."""),
            ("human", """Task: {task}

Work from other players to critique:
{other_work}

Provide your critique. Focus on:
1. Accuracy and correctness
2. Completeness
3. Clarity and specificity
4. Suggestions for improvement""")
        ])
        
        chain = prompt | self.llm | self._output_parser
        
        other_work_str = "\n\n".join([
            f"=== {name} ===\n{work}" 
            for name, work in other_players_work.items()
        ])
        
        return chain.invoke({
            "task": task,
            "other_work": other_work_str
        })
    
    def revise_work(
        self,
        task: str,
        my_original_work: str,
        critiques: List[str]
    ) -> str:
        """
        Revise work based on critiques received.

        Prompt:
            System:
                You are {self.name}. {self.role_prompt}

                You are revising your work based on feedback from other analysts.
                Incorporate valid criticisms while maintaining your unique perspective.

            Human:
                Task: {task}

                Your original work:
                {original_work}

                Critiques received:
                {critiques}

                Provide your revised analysis. Address the valid points raised in the critiques
                while maintaining accuracy and your analytical perspective.
        
        Args:
            task: The task being worked on
            my_original_work: This player's original work
            critiques: List of critiques from other players
            
        Returns:
            Revised work as a string
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are {self.name}. {self.role_prompt}

You are revising your work based on feedback from other analysts.
Incorporate valid criticisms while maintaining your unique perspective."""),
            ("human", """Task: {task}

Your original work:
{original_work}

Critiques received:
{critiques}

Provide your revised analysis. Address the valid points raised in the critiques
while maintaining accuracy and your analytical perspective.""")
        ])
        
        chain = prompt | self.llm | self._output_parser
        
        critiques_str = "\n\n".join([
            f"Critique {i+1}:\n{c}" 
            for i, c in enumerate(critiques)
        ])
        
        return chain.invoke({
            "task": task,
            "original_work": my_original_work,
            "critiques": critiques_str
        })

    def synthesize_results(
        self,
        task: str,
        all_results: List[Dict[str, Any]],
        output_schema: Optional[Type[BaseModel]] = None
    ) -> Union[str, BaseModel]:
        """
        Synthesize multiple results into a consolidated output.
        Uses this player's role/expertise to consolidate debate results.

        Prompt used for string output:
            System:
                You are {player_name}. {role_prompt}

                You are now synthesizing results from multiple analysts who
                worked on the same task.

                Your job:
                - Consolidate the findings into a single, authoritative result
                - Resolve conflicts by choosing the most accurate/complete information
                - Preserve important details while removing redundancy
                - Output a clear, concise result appropriate for the task

                Output requirements:
                - Output ONLY the consolidated result
                - NO meta-commentary like "Based on the analyses..." or "The players found..."
                - NO explanations of your synthesis process
                - Keep the format appropriate for the task

            Human:
                Task: {task}

                Results from all analysts:
                {all_results}

                Provide the consolidated result for this task. Output only the
                result, no commentary.

        Prompt used for structured output:
            System:
                You are {player_name}. {role_prompt}

                You are synthesizing results from multiple analysts into a
                structured format.

                Your job:
                - Extract and consolidate all relevant information from the analyses
                - Fill in ALL fields in the schema with concrete values from the gathered information
                - Use null/None for fields where information is truly unavailable
                - Resolve conflicts by choosing the most accurate/complete information

                Critical:
                - Output MUST conform exactly to the provided schema
                - Use actual values, not placeholders like "..."
                - Be specific and concrete

            Human:
                Task: {task}

                Results from all analysts:
                {all_results}

                Generate the final structured output.
        
        Args:
            task: The task that was worked on
            all_results: List of results from all players
            output_schema: Optional Pydantic model class for structured output.
                          If provided, returns a validated Pydantic model instance.
                          If None, returns a string (legacy behavior).
            
        Returns:
            Synthesized result as a string or Pydantic model instance
        """
        # Single-player, string output: there is nothing to consolidate, so return
        # the sole player's analysis verbatim. This makes single-player synthesis
        # deterministic and avoids a redundant LLM round-trip. The structured case
        # is excluded — it still needs the model to map the analysis onto the
        # schema, which is not a no-op even for one result.
        if output_schema is None and len(all_results) == 1:
            sole = all_results[0]
            return sole.get("analysis", str(sole))

        results_str = "\n\n".join([
            f"=== {r.get('player', 'Unknown')} ===\n{r.get('analysis', str(r))}"
            for r in all_results
        ])

        if output_schema is not None:
            # Use structured output with Pydantic schema
            return self._synthesize_structured(task, results_str, output_schema)
        else:
            # Legacy string output
            return self._synthesize_string(task, results_str)
    
    def _synthesize_string(self, task: str, results_str: str) -> str:
        """Synthesize results as a string (legacy behavior)."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are {self.name}. {self.role_prompt}

You are now synthesizing results from multiple analysts who worked on the same task.

**Your job:**
- Consolidate the findings into a single, authoritative result
- Resolve any conflicts by choosing the most accurate/complete information
- Preserve important details while removing redundancy
- Output a clear, concise result appropriate for the task

**Output requirements:**
- Output ONLY the consolidated result
- NO meta-commentary like "Based on the analyses..." or "The players found..."
- NO explanations of your synthesis process
- Keep the format appropriate for the task (e.g., numbers for counts, lists for columns)"""),
            ("human", """Task: {task}

Results from all analysts:
{all_results}

Provide the consolidated result for this task. Output only the result, no commentary.""")
        ])
        
        chain = prompt | self.llm | self._output_parser
        
        return chain.invoke({
            "task": task,
            "all_results": results_str
        })
    
    def _synthesize_structured(
        self, 
        task: str, 
        results_str: str, 
        output_schema: Type[BaseModel]
    ) -> BaseModel:
        """
        Synthesize results into a structured Pydantic model.
        
        Uses LangChain's with_structured_output() for guaranteed schema compliance.
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are {self.name}. {self.role_prompt}

You are synthesizing results from multiple analysts into a structured format.

**Your job:**
- Extract and consolidate all relevant information from the analyses
- Fill in ALL fields in the schema with concrete values from the gathered information
- Use null/None for fields where information is truly unavailable
- Resolve conflicts by choosing the most accurate/complete information

**CRITICAL:**
- Output MUST conform exactly to the provided schema
- Use actual values, not placeholders like "..." 
- Be specific and concrete"""),
            ("human", """Task: {task}

Results from all analysts:
{all_results}

Generate the final structured output.""")
        ])
        
        # Use with_structured_output for guaranteed schema compliance
        structured_llm = self.llm.with_structured_output(output_schema)
        chain = prompt | structured_llm
        
        return chain.invoke({
            "task": task,
            "all_results": results_str
        })
    
    def __repr__(self):
        return f"Player(name={self.name}, tools={len(self.tools)})"


def tools_for_role(config: Dict[str, Any], context: ExecutionContext) -> List[BaseTool]:
    """Resolve a role's requested toolsets against what a context can serve."""
    return resolve_toolsets(config.get("toolsets", []), context)


def create_player_from_config(
    config: Dict[str, Any],
    name: str,
    context: ExecutionContext,
    provider: Optional[str] = None,
    role_key: Optional[str] = None,
) -> Player:
    """
    Factory function to create a Player from a configuration dictionary.

    The role's ``toolsets`` are resolved against ``context``, so a player is
    only ever handed tools the context can actually serve.

    Args:
        config: Dictionary with 'role_prompt', 'toolsets', and optional
            'model_name', 'temperature'
        name: The name to assign to this player instance
        context: The ExecutionContext the player will operate on
        provider: LLM provider to use (default from config)
        role_key: Canonical player role key from PLAYER_CONFIGS

    Returns:
        Configured Player instance
    """
    resolved_role_key = role_key if role_key is not None else config.get("_role_key")
    return Player(
        name=name,
        role_prompt=config.get("role_prompt", "You are a helpful analyst."),
        tools=tools_for_role(config, context),
        model_name=config.get("model_name"),  # None means use config default
        temperature=config.get("temperature"),  # None means use config default
        provider=provider,
        role_key=resolved_role_key,
    )
