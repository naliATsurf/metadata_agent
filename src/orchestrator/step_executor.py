"""
Step-level execution with parallel players and debate.

This module implements the core logic for executing a single plan step:
1. Spawn multiple players (based on topology)
2. Each player executes the task in parallel
3. Players debate (critique and revise) their results
4. A synthesizer consolidates the final result

Uses the unified ExecutionContext abstraction for all data access.

The execution flow is:
    execute_parallel → critique → revise → [loop or synthesize]
"""
import logging
from typing import Dict, Any, List, Optional, Type

from pydantic import BaseModel
from langgraph.graph import StateGraph, END

from src.core.state import StepExecutionState, PlayerResult, DebateEntry
from ..players import Player, create_player_from_config, PLAYER_CONFIGS


# ===================================================================
#  NODE FUNCTIONS
# ===================================================================

def execute_parallel_node(state: StepExecutionState) -> Dict[str, Any]:
    """
    Execute the task with all players in parallel.
    
    Each player independently works on the same task using their tools
    and perspective. Results are collected for the debate phase.
    
    Uses DataSource for unified data access.
    """
    logging.info(f"--- STEP {state['step_index']}: PARALLEL EXECUTION ---")
    logging.info(f"Task: {state['task']}")
    logging.info(f"Players: {len(state['players'])}")
    
    players: List[Player] = state["players"]
    task = state["task"]
    context_key = state["context_key"]
    context_info = state["context_info"]
    target_tables = state.get("target_tables", [])
    workspace = state["workspace"]
    input_mappings = state["input_mappings"]
    
    is_multi_resource = context_info.get("is_multi_resource", False)
    
    if is_multi_resource:
        logging.info(
            f"  Multi-resource mode: {len(context_info.get('resources', []))} resources"
        )
        if target_tables:
            logging.info(f"  Target resources: {target_tables}")
    
    player_results: List[PlayerResult] = []
    initial_debate_entries: List[DebateEntry] = []
    
    for player in players:
        try:
            # Execute the task with ExecutionContext context
            result = player.execute_task(
                task=task,
                context_key=context_key,
                context_info=context_info,
                workspace=workspace,
                inputs=input_mappings,
                target_tables=target_tables
            )
            
            player_results.append({
                "player_name": player.name,
                "task": task,
                "tool_results": result.get("tool_results", {}),
                "analysis": result.get("analysis", ""),
                "success": result.get("success", True)
            })
            
            # Add to debate log as initial work
            initial_debate_entries.append({
                "round": 1,
                "player_name": player.name,
                "entry_type": "initial_work",
                "content": result.get("analysis", "")
            })
            
            logging.info(f"  Player '{player.name}' completed execution")
            # Log a preview of the output
            analysis_preview = result.get("analysis", "")[:200].replace('\n', ' ')
            if len(result.get("analysis", "")) > 200:
                analysis_preview += "..."
            logging.info(f"    Output: {analysis_preview}")
            
        except Exception as e:
            logging.error(f"  Player '{player.name}' failed: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
            player_results.append({
                "player_name": player.name,
                "task": task,
                "tool_results": {},
                "analysis": f"Error: {str(e)}",
                "success": False
            })
    
    return {
        "player_results": player_results,
        "debate_log": state.get("debate_log", []) + initial_debate_entries,
        "current_debate_round": 1
    }


def critique_node(state: StepExecutionState) -> Dict[str, Any]:
    """
    Each player critiques the work of other players.
    
    Players review each other's analyses and provide constructive feedback
    to improve the overall quality of the result.
    """
    current_round = state["current_debate_round"]
    logging.info(f"--- STEP {state['step_index']}: CRITIQUE (Round {current_round}) ---")
    
    players: List[Player] = state["players"]
    task = state["task"]
    debate_log = state["debate_log"]
    
    # Get work from the current round
    current_round_work = {
        entry["player_name"]: entry["content"]
        for entry in debate_log
        if entry["round"] == current_round and "work" in entry["entry_type"]
    }
    
    new_entries: List[DebateEntry] = []
    
    for player in players:
        # Don't critique own work
        other_work = {k: v for k, v in current_round_work.items() if k != player.name}
        
        if not other_work:
            continue
            
        try:
            critique = player.critique_work(task=task, other_players_work=other_work)
            new_entries.append({
                "round": current_round,
                "player_name": player.name,
                "entry_type": "critique",
                "content": critique
            })
            logging.info(f"  Player '{player.name}' provided critique")
        except Exception as e:
            logging.error(f"  Player '{player.name}' critique failed: {str(e)}")
    
    return {"debate_log": debate_log + new_entries}


def revise_node(state: StepExecutionState) -> Dict[str, Any]:
    """
    Each player revises their work based on critiques.
    
    Players incorporate feedback from critiques to improve their analysis.
    """
    current_round = state["current_debate_round"]
    next_round = current_round + 1
    logging.info(f"--- STEP {state['step_index']}: REVISION (Round {next_round}) ---")
    
    players: List[Player] = state["players"]
    task = state["task"]
    debate_log = state["debate_log"]
    
    # Get this round's critiques
    critiques = [
        entry["content"]
        for entry in debate_log
        if entry["round"] == current_round and entry["entry_type"] == "critique"
    ]
    
    new_entries: List[DebateEntry] = []
    updated_results: List[PlayerResult] = []
    
    for player in players:
        # Find player's original work
        original_work = next(
            (entry["content"] for entry in debate_log
             if entry["player_name"] == player.name 
             and "work" in entry["entry_type"]
             and entry["round"] == current_round),
            ""
        )
        
        try:
            revised = player.revise_work(
                task=task,
                my_original_work=original_work,
                critiques=critiques
            )
            new_entries.append({
                "round": next_round,
                "player_name": player.name,
                "entry_type": "revised_work",
                "content": revised
            })
            
            # Update player results with revised work
            updated_results.append({
                "player_name": player.name,
                "task": task,
                "tool_results": {},  # Tools already ran in initial execution
                "analysis": revised,
                "success": True
            })
            
            logging.info(f"  Player '{player.name}' revised their work")
        except Exception as e:
            logging.error(f"  Player '{player.name}' revision failed: {str(e)}")
    
    return {
        "debate_log": debate_log + new_entries,
        "current_debate_round": next_round,
        "player_results": updated_results if updated_results else state["player_results"]
    }


def synthesize_node(state: StepExecutionState) -> Dict[str, Any]:
    """
    Synthesize all player results into a consolidated output.
    
    The synthesizer combines the best insights from all players
    and resolves any conflicts to produce a final result.
    
    If output_schema is provided in state, uses structured output
    to return a validated Pydantic model instead of a string.
    """
    logging.info(f"--- STEP {state['step_index']}: SYNTHESIS ---")
    
    synthesizer: Player = state["synthesizer"]
    task = state["task"]
    player_results = state["player_results"]
    expected_outputs = state["expected_outputs"]
    output_schema: Optional[Type[BaseModel]] = state.get("output_schema")
    
    if output_schema:
        logging.info(f"  Using structured output with schema: {output_schema.__name__}")
    
    try:
        # Prepare results for synthesis
        results_for_synthesis = [
            {
                "player": r["player_name"],
                "analysis": r["analysis"],
                "tool_results": r["tool_results"]
            }
            for r in player_results
        ]
        
        # Synthesize with optional structured output
        consolidated = synthesizer.synthesize_results(
            task=task,
            all_results=results_for_synthesis,
            output_schema=output_schema
        )
        
        # Convert Pydantic model to dict for artifact storage if needed
        if output_schema and isinstance(consolidated, BaseModel):
            artifact_value = consolidated.model_dump(by_alias=True)
            logging.info(f"  Structured output validated successfully")
        else:
            artifact_value = consolidated
        
        # Create artifacts from the consolidated result
        produced_artifacts = {}
        for output_name in expected_outputs:
            produced_artifacts[output_name] = artifact_value
        
        logging.info(f"  Synthesis complete. Produced artifacts: {list(produced_artifacts.keys())}")
        # Log a preview of the synthesized output
        if isinstance(artifact_value, dict):
            preview = str(artifact_value)[:200].replace('\n', ' ')
        else:
            preview = str(artifact_value)[:200].replace('\n', ' ')
        if len(str(artifact_value)) > 200:
            preview += "..."
        logging.info(f"    Synthesized output: {preview}")
        
        return {
            "consolidated_result": consolidated,
            "produced_artifacts": produced_artifacts
        }
        
    except Exception as e:
        error_msg = f"Synthesis failed: {str(e)}"
        logging.error(f"  {error_msg}")
        import traceback
        logging.error(traceback.format_exc())
        return {
            "error": error_msg,
            "consolidated_result": None,
            "produced_artifacts": {}
        }


def debate_router(state: StepExecutionState) -> str:
    """
    Decide whether to continue debate or synthesize.
    
    Routes to:
    - 'critique_node': If more debate rounds needed
    - 'synthesize_node': If max rounds reached or single player
    - '__end__': If there's an error
    """
    if state.get("error"):
        logging.error(f"Error detected, ending step: {state['error']}")
        return "__end__"
    
    current_round = state["current_debate_round"]
    max_rounds = state["max_debate_rounds"]
    num_players = len(state["players"])
    
    # No debate needed for single player
    if num_players <= 1:
        logging.info("Single player, skipping debate")
        return "synthesize_node"
    
    # Check if we should continue debating
    if current_round < max_rounds:
        logging.info(f"Debate round {current_round}/{max_rounds}, continuing to critique")
        return "critique_node"
    else:
        logging.info(f"Max debate rounds ({max_rounds}) reached, synthesizing")
        return "synthesize_node"


# ===================================================================
#  GRAPH CONSTRUCTION
# ===================================================================

def get_step_execution_graph():
    """
    Constructs and compiles the StateGraph for step execution.
    
    The graph flow is:
        execute_parallel → [debate_router] → critique → revise → [debate_router] → ... → synthesize
    
    Returns:
        A compiled langgraph application for executing a single step.
    """
    graph = StateGraph(StepExecutionState)
    
    # Add nodes
    graph.add_node("execute_parallel_node", execute_parallel_node)
    graph.add_node("critique_node", critique_node)
    graph.add_node("revise_node", revise_node)
    graph.add_node("synthesize_node", synthesize_node)
    
    # Set entry point
    graph.set_entry_point("execute_parallel_node")
    
    # Define edges
    # After parallel execution, route based on debate config
    graph.add_conditional_edges(
        "execute_parallel_node",
        debate_router,
        {
            "critique_node": "critique_node",
            "synthesize_node": "synthesize_node",
            "__end__": END
        }
    )
    
    # After critique, always revise
    graph.add_edge("critique_node", "revise_node")
    
    # After revision, route again
    graph.add_conditional_edges(
        "revise_node",
        debate_router,
        {
            "critique_node": "critique_node",
            "synthesize_node": "synthesize_node",
            "__end__": END
        }
    )
    
    # After synthesis, end
    graph.add_edge("synthesize_node", END)
    
    return graph.compile()


# ===================================================================
#  HELPER FUNCTIONS
# ===================================================================

def create_step_state(
    step_index: int,
    step_dict: Dict[str, Any],
    context: Any,
    context_key: str,
    workspace: Dict[str, Any],
    metadata_standard: str,
    players_per_step: int,
    debate_rounds: int,
    player_pool: List[str],
    output_schema: Optional[Type[BaseModel]] = None
) -> StepExecutionState:
    """
    Create the initial state for executing a step.
    
    Args:
        step_index: Index of this step in the plan
        step_dict: The step dictionary from the plan
        context: The ExecutionContext to analyze
        context_key: Key for the ExecutionContext in the tool registry
        workspace: Current workspace with artifacts
        metadata_standard: The metadata standard to follow
        players_per_step: Number of players to spawn
        debate_rounds: Number of debate rounds
        player_pool: List of player role names available
        output_schema: Optional Pydantic model for structured output (typically for final step)
        
    Returns:
        Initialized StepExecutionState
    """
    # Get the player role specified in the plan for this step
    specified_player_role = step_dict.get("player", "")
    
    # Create player instances - all of the same type specified in the plan
    players = []
    
    # Determine which player role to use
    if specified_player_role and specified_player_role in PLAYER_CONFIGS:
        role_to_use = specified_player_role
    elif player_pool:
        role_to_use = player_pool[0]  # Fallback to first in pool
    else:
        role_to_use = "data_analyst"  # Ultimate fallback
    
    # Create multiple instances of the same player type
    config = PLAYER_CONFIGS.get(role_to_use, {})
    for i in range(players_per_step):
        player = create_player_from_config(config, name=f"{role_to_use}_{i+1}")
        players.append(player)
    
    logging.info(f"  Created {players_per_step} '{role_to_use}' player(s)")
    
    # Use the first player as synthesizer
    synthesizer = players[0] if players else None
    
    # Get target resources from step (field still named target_tables)
    target_tables = step_dict.get("target_tables", [])
    
    return StepExecutionState(
        step_index=step_index,
        task=step_dict.get("task", ""),
        player_name=step_dict.get("player", ""),
        rationale=step_dict.get("rationale", ""),
        input_mappings=step_dict.get("inputs", {}),
        expected_outputs=step_dict.get("outputs", []),
        target_tables=target_tables,
        context_key=context_key,
        context_info=context.to_dict(),
        workspace=workspace,
        metadata_standard=metadata_standard,
        players=players,
        synthesizer=synthesizer,
        max_debate_rounds=debate_rounds,
        current_debate_round=0,
        player_results=[],
        debate_log=[],
        output_schema=output_schema,
        consolidated_result=None,
        produced_artifacts={},
        error=None,
    )
