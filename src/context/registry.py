"""
Shared context type registry and helper utilities.
"""

from pathlib import Path

from .base_context import ContextType


EXTENSION_MAP = {
    ".csv": ContextType.SINGLE_CSV,
    ".tsv": ContextType.SINGLE_CSV,
    ".txt": ContextType.TEXT,
    ".sqlite": ContextType.SQLITE,
    ".sqlite3": ContextType.SQLITE,
    ".db": ContextType.SQLITE,
}


def detect_type_from_extension(path: str) -> ContextType:
    """Detect context type from file extension."""
    ext = Path(path).suffix.lower()
    return EXTENSION_MAP.get(ext, ContextType.UNKNOWN)


def is_csv_type(context_type: ContextType) -> bool:
    """Return True if a context type is a CSV variant."""
    return context_type in {ContextType.SINGLE_CSV, ContextType.MULTI_CSV}
