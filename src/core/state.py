"""
Defines the state schemas for the multi-agent system.

This module contains TypedDict definitions for:
1. StepExecutionState: State for executing a single plan step with parallel players
2. PlanExecutionState: Top-level state for executing an entire plan

Uses the unified ExecutionContext abstraction for all data access.
"""
from typing import TypedDict, List, Dict, Any, Optional, Type

from pydantic import BaseModel

from src.core.schemas import Task


class PlayerResult(TypedDict):
    """Result from a single player's execution."""
    player_name: str
    task: str
    tool_results: Dict[str, Any]
    analysis: str
    success: bool


class DebateEntry(TypedDict):
    """A single entry in the debate log."""
    round: int
    player_name: str
    entry_type: str  # 'initial_work', 'critique', 'revised_work'
    content: str


class StepExecutionState(TypedDict):
    """
    State for executing a single plan step with multiple parallel players.
    
    This state is used by the step-level debate graph where:
    1. Multiple players execute the same task in parallel
    2. Players debate (critique and revise) their results
    3. One of the players synthesizes the final result using their role expertise
    
    Uses unified DataSource for all data access.
    """
    # --- Step Configuration ---
    step_index: int                    # Index of this step in the plan
    task: str                          # The task description
    player_name: str                   # The player type for this step (from plan)
    rationale: str                     # Why this step is needed
    input_mappings: Dict[str, str]     # Maps param names to artifact names
    expected_outputs: List[str]        # Artifact names this step should produce
    target_tables: List[str]           # Which resources this step targets (empty = all)
    
    # --- Execution Context ---
    context_key: str                   # Key to registered ExecutionContext in tool registry
    context_info: Dict[str, Any]       # Serialized ExecutionContext info
    workspace: Dict[str, Any]          # Artifacts from previous steps
    metadata_standard: str             # The metadata standard to follow
    
    # --- Player Configuration ---
    players: List[Any]                 # List of Player instances for this step
    synthesizer: Any                   # Player instance for synthesis (one of the players)
    
    # --- Debate Configuration ---
    max_debate_rounds: int             # Maximum debate rounds for this step
    current_debate_round: int          # Current debate round (starts at 1)
    
    # --- Dynamic State ---
    player_results: List[PlayerResult] # Results from parallel execution
    debate_log: List[DebateEntry]      # Log of debate entries
    
    # --- Structured Output ---
    output_schema: Optional[Type[BaseModel]]  # Pydantic schema for structured output (final step only)
    
    # --- Output ---
    consolidated_result: Optional[Any]  # Final synthesized result (str or BaseModel)
    produced_artifacts: Dict[str, Any]  # Artifacts produced by this step
    error: Optional[str]                # Error message if something went wrong


class PlanExecutionState(TypedDict):
    """
    Top-level state for executing an entire plan.
    
    This state tracks progress through all steps in a plan,
    accumulating artifacts in the workspace as steps complete.
    
    Uses unified DataSource for all data access.
    """
    # --- Plan Configuration ---
    plan_steps: List[Task]             # The steps from the Plan object
    current_step_index: int            # Which step we're on (0-indexed)
    
    # --- Execution Context ---
    context_key: str                   # Key to registered ExecutionContext in tool registry
    context_info: Dict[str, Any]       # Serialized ExecutionContext info
    metadata_standard: str             # The metadata standard to follow
    
    # --- Topology Configuration ---
    topology_name: str                 # Name of the execution topology
    players_per_step: int              # How many players per step
    debate_rounds: int                 # Debate rounds per step
    player_pool: List[str]             # Available player role names
    
    # --- Accumulated State ---
    workspace: Dict[str, Any]          # Artifacts accumulated from all steps
    step_results: List[Dict[str, Any]] # Results from each completed step
    
    # --- Context Results ---
    resource_metadata: Dict[str, Any]  # Per-resource metadata results
    discovered_relationships: List[Dict[str, Any]]  # Discovered relationships
    
    # --- Output ---
    final_output: Optional[Dict[str, Any]]  # Final metadata output
    error: Optional[str]               # Error if execution failed
