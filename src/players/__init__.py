"""
Players module for the multi-agent system.

This module provides:
- Player: Unified player class that can execute tasks and participate in debates
- create_player_from_config: Factory function for creating players
- PLAYER_CONFIGS: Configuration dictionaries for available player roles

Typical usage:
    from src.players import Player, create_player_from_config, PLAYER_CONFIGS
    
    # Create a player from config
    player = create_player_from_config(
        PLAYER_CONFIGS["data_analyst"],
        name="analyst_1"
    )
    
    # Execute a task with ExecutionContext (used internally by orchestrator)
    result = player.execute_task(
        task="Analyze dataset structure",
        context_key="ctx_abc123",  # Key to registered ExecutionContext
        context_info={"name": "my_dataset", "resources": ["users"], ...},
        workspace={},
        inputs={}
    )
"""

from .player import Player, create_player_from_config
from .configs import PLAYER_CONFIGS

__all__ = [
    "Player",
    "create_player_from_config",
    "PLAYER_CONFIGS",
]
