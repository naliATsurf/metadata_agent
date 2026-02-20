"""
Orchestrator module for the multi-agent metadata extraction system.

This module provides:
- Orchestrator: Main class for planning and executing metadata extraction
- PlanExecutor: Executes a complete plan with parallel players
- Step execution via LangGraph

Uses the unified ExecutionContext abstraction for all data access.
See `src.context` for context creation and configuration.

Typical usage:
    from src.orchestrator import Orchestrator
    from src.standards import METADATA_STANDARDS
    
    # Create orchestrator
    orchestrator = Orchestrator(topology_name="default")
    
    # Run on any data source (auto-detected)
    result = orchestrator.run(
        source="./data/users.csv",  # Single file
        metadata_standard=METADATA_STANDARDS["basic"]
    )
    
    # Or multiple files
    result = orchestrator.run(
        source={
            "users": "./data/users.csv",
            "orders": "./data/orders.csv"
        },
        metadata_standard=METADATA_STANDARDS["relational"]
    )
    
    # Or SQLite database
    result = orchestrator.run(
        source="./data/mydb.sqlite",
        metadata_standard=METADATA_STANDARDS["relational"]
    )
    
    # Or directory of CSVs
    result = orchestrator.run(
        source="./data/my_dataset/",
        metadata_standard=METADATA_STANDARDS["relational"]
    )
"""

from .orchestrator import Orchestrator, run_metadata_extraction
from .plan_executor import PlanExecutor, execute_plan
from src.core.schemas import Plan, Task, StepResult, ExecutionResult
from src.core.state import StepExecutionState, PlanExecutionState

__all__ = [
    # Main classes
    "Orchestrator",
    "PlanExecutor",
    # Convenience functions
    "run_metadata_extraction",
    "execute_plan",
    # Schema classes
    "Plan",
    "Task",
    "StepResult",
    "ExecutionResult",
    # State classes
    "StepExecutionState",
    "PlanExecutionState",
]
