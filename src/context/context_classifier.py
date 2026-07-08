"""
classify context types based on the input data/files
"""
import os
from typing import List

from src.context.base_context import ContextType
from src.context.registry import (
    detect_type_from_extension,
    is_csv_type,
    is_text_type,
)


def _single_multi_csv_classifier(path_list: List[str]) -> ContextType:
    """Classify csv context by number of files."""
    if len(path_list) == 1:
        return ContextType.SINGLE_CSV
    return ContextType.MULTI_CSV


def classify_context_type(input_list: List[str]) -> ContextType:
    """
    Classify the context type based on the input list.
    Minimal helper used by context-layer components.
    """
    if not input_list:
        return ContextType.UNKNOWN

    input_list = [os.path.expanduser(p) for p in input_list]

    if len(input_list) == 1:
        input_path = input_list[0]
        if os.path.isdir(input_path):
            types = [
                detect_type_from_extension(f) for f in os.listdir(input_path)
            ]
            csv_files = [t for t in types if is_csv_type(t)]
            if csv_files:
                return _single_multi_csv_classifier(csv_files)
            if any(is_text_type(t) for t in types):
                return ContextType.TEXT
            return ContextType.UNKNOWN
        return detect_type_from_extension(input_path)

    # Multi-path input: require a homogeneous modality
    types = [detect_type_from_extension(path) for path in input_list]
    if all(is_csv_type(t) for t in types):
        return ContextType.MULTI_CSV
    if all(is_text_type(t) for t in types):
        return ContextType.TEXT

    return ContextType.UNKNOWN
