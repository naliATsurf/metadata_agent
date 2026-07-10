"""
Tools for row/column contexts.

Keyed on the *capability* (``TabularContext``), not on a file format — these
serve CSV, SQLite, and any future tabular source without modification.

Importing this package registers its tools.
"""

from src.tools.tabular import profiling, spatial, temporal  # noqa: F401

__all__ = ["profiling", "spatial", "temporal"]
