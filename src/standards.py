"""
This file contains predefined, named metadata standards that can be used by the orchestrator.

Each standard has:
1. A string template (METADATA_STANDARDS) - used for prompting the LLM
2. A Pydantic model (METADATA_SCHEMAS) - used for structured output validation
"""

from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel, Field


# =============================================================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUT
# =============================================================================

class SpatialEcologicalMetadata(BaseModel):
    """Simple spatial/ecological metadata standard."""
    title: str = Field(description="Title of the dataset")
    description: str = Field(description="Description of the dataset")
    subject: Optional[str] = Field(default=None, description="Subject/topic")
    spatial_coverage: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Geographic bounding box with keys: "
            "min_lat, min_lon, max_lat, max_lon"
        ),
    )
    spatial_resolution: Optional[str] = Field(default=None, description="Spatial resolution of the data")
    temporal_coverage: Optional[str] = Field(default=None, description="Time period covered")
    temporal_resolution: Optional[str] = Field(default=None, description="Temporal resolution of the data")
    methods: Optional[str] = Field(default=None, description="Methods used for data collection")
    format: Optional[str] = Field(default=None, description="Data format")


# =============================================================================
# SCHEMA REGISTRY - Maps standard names to Pydantic models
# =============================================================================

METADATA_SCHEMAS: Dict[str, type[BaseModel]] = {
    "spatial_ecological": SpatialEcologicalMetadata,
}


def get_schema_for_standard(standard_name: str) -> Optional[type[BaseModel]]:
    """
    Get the Pydantic schema class for a given standard name.
    
    Args:
        standard_name: Name of the metadata standard
        
    Returns:
        Pydantic model class, or None if not found
    """
    return METADATA_SCHEMAS.get(standard_name)



METADATA_STANDARDS = {
    "spatial_ecological": """
{
    "title": "...",
    "description": "...",
    "subject": "...",
    "spatial_coverage": "Geographic bounding box in WGS84 with numeric fields: min_lat, min_lon, max_lat, max_lon",
    "spatial_resolution": "...",
    "temporal_coverage": "...",
    "temporal_resolution": "...",
    "methods": "...",
    "format": "..."
}
"""
}


def load_metadata_standard(standard_arg: str) -> str:
    """
    Load metadata standard content from the registry or a file path.
    """
    if standard_arg in METADATA_STANDARDS:
        return METADATA_STANDARDS[standard_arg]

    standard_path = Path(standard_arg)
    if standard_path.exists():
        return standard_path.read_text()

    raise ValueError(
        f"Metadata standard '{standard_arg}' not found as a predefined standard or as a valid file path."
    )
