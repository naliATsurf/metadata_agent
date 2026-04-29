import hashlib
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from src.context import create_context
from src.orchestrator import Orchestrator
from src.standards import METADATA_STANDARDS, load_metadata_standard
from src.topology import EXECUTION_TOPOLOGIES


SUPPORTED_FILE_TYPES = ["csv", "tsv", "txt"]


def available_metadata_standards() -> list[str]:
    """Return the names of metadata standards available to the demo.

    Returns:
        A list of metadata standard names.
    """
    return list(METADATA_STANDARDS)


def available_topologies() -> list[str]:
    """Return the names of execution topologies available to the demo.

    Returns:
        A list of execution topology names.
    """
    return list(EXECUTION_TOPOLOGIES)


def uploaded_file_key(file_bytes: bytes, standard_name: str) -> str:
    """Build a stable cache key for an uploaded file and selected standard.

    Args:
        file_bytes: Raw bytes from the uploaded file.
        standard_name: Name of the selected metadata standard.

    Returns:
        A cache key combining the file hash and metadata standard name.
    """
    digest = hashlib.sha256(file_bytes).hexdigest()
    return f"{digest}:{standard_name}"


def load_preview(file_name: str, file_bytes: bytes, rows: int = 25) -> pd.DataFrame:
    """Load the first rows of a CSV or TSV upload for preview display.

    Args:
        file_name: Name of the uploaded file, used to infer the delimiter.
        file_bytes: Raw bytes from the uploaded file.
        rows: Maximum number of rows to load.

    Returns:
        A pandas DataFrame containing the preview rows.
    """
    separator = "\t" if file_name.lower().endswith(".tsv") else ","
    return pd.read_csv(BytesIO(file_bytes), sep=separator, nrows=rows)


def generate_metadata(
    file_name: str,
    file_bytes: bytes,
    standard_name: str,
    topology_name: str = "default",
) -> dict[str, Any]:
    """Run metadata extraction for an uploaded file and return JSON-friendly output.

    The uploaded bytes are written to a temporary file because the extraction
    orchestrator works with filesystem paths. The temporary file is removed
    after extraction, even if the orchestrator raises an error.

    Args:
        file_name: Name of the uploaded file.
        file_bytes: Raw bytes from the uploaded file.
        standard_name: Name of the metadata standard to use.
        topology_name: Name of the execution topology to use.

    Returns:
        The extraction result converted to JSON-friendly values.

    Raises:
        ValueError: If the metadata standard name is not supported.
        ValueError: If the topology name is not supported.
        RuntimeError: If the metadata agent does not return a result.
    """
    if standard_name not in METADATA_STANDARDS:
        raise ValueError(f"Unknown metadata standard: {standard_name}")
    if topology_name not in EXECUTION_TOPOLOGIES:
        raise ValueError(f"Unknown execution topology: {topology_name}")

    temp_path = _write_upload_to_temp(file_name, file_bytes)
    try:
        dataset_name = Path(file_name).stem
        metadata_standard = load_metadata_standard(standard_name)
        context = create_context(temp_path, name=dataset_name)
        orchestrator = Orchestrator(topology_name=topology_name)

        result = orchestrator.run(
            source=context,
            metadata_standard=metadata_standard,
            metadata_standard_name=standard_name,
        )
        if result is None:
            raise RuntimeError("Metadata agent did not return a result.")
        return _to_displayable(result)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def extract_metadata(result: dict[str, Any]) -> Any:
    """Return the final metadata payload from an extraction result.

    Args:
        result: Metadata extraction result.

    Returns:
        The final metadata payload, or None if it is not present.
    """
    return result.get("final_metadata") or result.get("final_workspace", {}).get("metadata_output")


def execution_details(result: dict[str, Any]) -> dict[str, Any]:
    """Select execution details that are useful for display and debugging.

    Args:
        result: Metadata extraction result.

    Returns:
        A dictionary with context, resource metadata, and relationship details.
    """
    return {
        "context_info": result.get("context_info"),
        "resource_metadata": result.get("resource_metadata"),
        "relationships": result.get("relationships"),
    }


def _write_upload_to_temp(file_name: str, file_bytes: bytes) -> str:
    """Persist uploaded bytes to a temporary file and return its path.

    Args:
        file_name: Name of the uploaded file, used to preserve the suffix.
        file_bytes: Raw bytes from the uploaded file.

    Returns:
        Path to the temporary file.
    """
    suffix = Path(file_name).suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        return tmp.name


def _to_displayable(value: Any) -> Any:
    """Convert extraction output into values Streamlit can render as JSON.

    Args:
        value: Extraction output or nested value to convert.

    Returns:
        A JSON-friendly representation of the value.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return json.loads(json.dumps(value, default=str))
