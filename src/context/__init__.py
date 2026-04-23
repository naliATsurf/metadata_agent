"""
ExecutionContext Module - Unified context layer.

This module provides a unified interface for defining the "world" in which
the multi-agent system operates. This could be a traditional data source,
a file system, an API, etc.

The ExecutionContext abstraction allows the rest of the system to work with
the environment in a consistent way.

Quick Start:
    from src.context import create_context
    
    # Single CSV file
    ctx = create_context("./data/users.csv")
    
    # Multiple related CSVs
    ctx = create_context({
        "users": "./data/users.csv",
        "orders": "./data/orders.csv"
    })
    
    # SQLite database
    ctx = create_context("./data/mydb.sqlite")
    
    # Use the ExecutionContext
    print(ctx.resources)
    df = ctx.read_resource("users")
    info = ctx.get_resource_info("users")
    schema = ctx.get_schema()
"""

from .base_context import (
    ExecutionContext,
    ContextType,
    ResourceInfo,
    FieldInfo,
    RelationshipInfo
)
from .csv_context import CSVContext
from .sqlite_context import SQLiteContext
from .context_factory import ContextFactory, create_context
from .context_classifier import classify_context_type
from .registry import EXTENSION_MAP, detect_type_from_extension, is_csv_type

__all__ = [
    # Base classes and models
    "ExecutionContext",
    "ContextType",
    "ResourceInfo",
    "FieldInfo",
    "RelationshipInfo",
    # Concrete implementations
    "CSVContext",
    "SQLiteContext",
    # Factory
    "ContextFactory",
    "create_context",
    # Classification and registry helpers
    "classify_context_type",
    "EXTENSION_MAP",
    "detect_type_from_extension",
    "is_csv_type",
]