"""
Predefined metadata standards used by the orchestrator.

Each standard is defined once in :data:`STANDARD_DEFINITIONS`. From that single
source of truth this module derives:

1. :data:`METADATA_STANDARDS`, string templates used for LLM prompting.
2. :data:`METADATA_SCHEMAS`, Pydantic models used for structured output
   validation.

Supported metadata standards
============================

``spatial_ecological``
    Metadata for spatial and ecological datasets.

    Fields:
        - ``title`` (``str``): Title of the dataset.
        - ``description`` (``str``): Description of the dataset.
        - ``subject`` (``Optional[str]``): Subject/topic.
        - ``spatial_coverage`` (``Optional[Dict[str, float]]``): Geographic
          bounding box with keys: ``min_lat``, ``min_lon``, ``max_lat``,
          ``max_lon``.
        - ``spatial_resolution`` (``Optional[str]``): Spatial resolution of the
          data.
        - ``temporal_coverage`` (``Optional[str]``): Time period covered, from
          and to date.
        - ``temporal_resolution`` (``Optional[str]``): Temporal resolution of
          the data.
        - ``methods`` (``Optional[str]``): Methods used for data collection.
        - ``format`` (``Optional[str]``): Data format.

``dummy_standard``
    Minimal metadata standard used for tests and TUI multi-selection behavior.

    Fields:
        - ``title`` (``str``): Dummy title field.
        - ``summary`` (``Optional[str]``): Dummy summary field.
        - ``owner`` (``Optional[str]``): Dummy owner field.
        - ``version`` (``Optional[str]``): Dummy version field.
"""

from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, create_model


# =============================================================================
# SINGLE SOURCE OF TRUTH
# Define each standard once, then derive both prompt template and Pydantic schema.
# =============================================================================

STANDARD_DEFINITIONS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "spatial_ecological": {
        "title": {
            "type": str,
            "default": ...,
            "description": "Title of the dataset",
            "prompt_hint": "...",
        },
        "description": {
            "type": str,
            "default": ...,
            "description": "Description of the dataset",
            "prompt_hint": "...",
        },
        "subject": {
            "type": Optional[str],
            "default": None,
            "description": "Subject/topic",
            "prompt_hint": "...",
        },
        "spatial_coverage": {
            "type": Optional[Dict[str, float]],
            "default": None,
            "description": (
                "Geographic bounding box with keys: "
                "min_lat, min_lon, max_lat, max_lon"
            ),
            # Keep the exact prompt wording requested.
            "prompt_hint": (
                "Geographic bounding box in WGS84 with numeric fields: "
                "min_lat, min_lon, max_lat, max_lon"
            ),
        },
        "spatial_resolution": {
            "type": Optional[str],
            "default": None,
            "description": "Spatial resolution of the data",
            "prompt_hint": "...",
        },
        "temporal_coverage": {
            "type": Optional[str],
            "default": None,
            "description": "Time period covered, from and to date",
            "prompt_hint": "Time period covered, from and to date",
        },
        "temporal_resolution": {
            "type": Optional[str],
            "default": None,
            "description": "Temporal resolution of the data",
            "prompt_hint": "Temporal resolution of the data, e.g. daily, monthly, yearly",
        },
        "methods": {
            "type": Optional[str],
            "default": None,
            "description": "Methods used for data collection",
            "prompt_hint": "Methods used for data collection",
        },
        "format": {
            "type": Optional[str],
            "default": None,
            "description": "Data format",
            "prompt_hint": "...",
        },
    },
    # Field-router test standard. Its fields deliberately span the three routing
    # buckets so the field-driven planner (docs/development/plan_field_router.md)
    # can be exercised end to end against data/tests/router_test/:
    #   - narrative fields answerable only from README.md (title, abstract, ...);
    #   - structural fields computed from observations.csv (record_count, ...);
    #   - ambiguous-structural fields whose opaque columns (la, lo, tmp) must be
    #     resolved through codebook.csv before they can be filled.
    # It also plants two conflicts: record_count (README says 1,000; the CSV has
    # 200) and temperature_units (codebook says Kelvin; values are clearly Celsius).
    "field_router_test": {
        "title": {
            "type": str,
            "default": ...,
            "description": "Title of the dataset",
            "prompt_hint": "The dataset's title, as stated in its description document",
        },
        "abstract": {
            "type": Optional[str],
            "default": None,
            "description": "A prose summary of what the dataset contains and why",
            "prompt_hint": "A short abstract summarising the dataset's purpose and content",
        },
        "methodology": {
            "type": Optional[str],
            "default": None,
            "description": "How the data was collected",
            "prompt_hint": "The survey or collection method described for the dataset",
        },
        "creator": {
            "type": Optional[str],
            "default": None,
            "description": "The person or organisation responsible for the dataset",
            "prompt_hint": "The dataset's creator or compiling organisation",
        },
        "funding": {
            "type": Optional[str],
            "default": None,
            "description": "Funding source and grant reference, if any",
            "prompt_hint": "The funder and grant number supporting the work",
        },
        "license": {
            "type": Optional[str],
            "default": None,
            "description": "The licence under which the data may be reused",
            "prompt_hint": "The reuse licence, e.g. CC BY 4.0",
        },
        "record_count": {
            "type": Optional[int],
            "default": None,
            "description": "The number of observation records in the data",
            "prompt_hint": "The count of rows in the observations table",
        },
        "variables": {
            "type": Optional[str],
            "default": None,
            "description": (
                "The measured variables and what each means; requires resolving "
                "opaque column names against the data dictionary"
            ),
            "prompt_hint": "The measured variables and their meanings, from the codebook",
        },
        "spatial_coverage": {
            "type": Optional[Dict[str, float]],
            "default": None,
            "description": (
                "Geographic bounding box with keys min_lat, min_lon, max_lat, "
                "max_lon; requires identifying the latitude and longitude columns"
            ),
            "prompt_hint": (
                "Geographic bounding box in WGS84 with numeric fields: "
                "min_lat, min_lon, max_lat, max_lon"
            ),
        },
        "temporal_coverage": {
            "type": Optional[str],
            "default": None,
            "description": "The time period the observations span, from and to date",
            "prompt_hint": "The date range covered by the observations",
        },
        "temperature_units": {
            "type": Optional[str],
            "default": None,
            "description": (
                "The units of the recorded air temperature; the data dictionary's "
                "stated units should be cross-checked against the actual values"
            ),
            "prompt_hint": "The units the air-temperature column is recorded in",
        },
    },
    # Dummy standard for testing @standard multi-selection behavior in TUI.
    "dummy_standard": {
        "title": {
            "type": str,
            "default": ...,
            "description": "Dummy title field",
            "prompt_hint": "...",
        },
        "summary": {
            "type": Optional[str],
            "default": None,
            "description": "Dummy summary field",
            "prompt_hint": "...",
        },
        "owner": {
            "type": Optional[str],
            "default": None,
            "description": "Dummy owner field",
            "prompt_hint": "...",
        },
        "version": {
            "type": Optional[str],
            "default": None,
            "description": "Dummy version field",
            "prompt_hint": "...",
        },
    },
    # ShareTrait "basic" standard. One field per *ShareTrait attribute_name*
    # (grouped by the property it belongs to), with each field's description taken
    # from that attribute's row in the full attribute-definition table. `creator`
    # and `anatomy` are omitted (no attribute of their own). Where an attribute name
    # occurs in more than one table, the definition is taken from the sense matching
    # its property group: `trait_type` / `trait_unit` from the measurement/trait
    # value context (table8/9), not the trait-catalogue category. Field types are
    # mapped from the dictionary's `type` column (DECIMAL → float, INT → int,
    # VARCHAR/TEXT → str). Its `requirement` column drives optionality: mandatory
    # attributes are required (non-Optional type, no default); optional ones are
    # Optional and default to None.
    "sharetrait_basic": {
        # identifier
        "doi_dataset": {
            "type": str,
            "default": ...,
            "description": "dataset DOI, provided in URL",
            "prompt_hint": "The dataset DOI (ShareTrait: doi_dataset)",
        },
        # description
        "title_dataset": {
            "type": str,
            "default": ...,
            "description": "title of the dataset. It is usually obtained from the title attribute of the original data source.",
            "prompt_hint": "The dataset title and summary (ShareTrait: title_dataset)",
        },
        # taxonomy
        "species_reported": {
            "type": str,
            "default": ...,
            "description": "species name given based on the study",
            "prompt_hint": "Reported species (ShareTrait taxonomy group)",
        },
        "genus_name": {
            "type": Optional[str],
            "default": None,
            "description": "genus level of the scientific taxonomic name",
            "prompt_hint": "Genus (ShareTrait taxonomy group)",
        },
        "family_name": {
            "type": Optional[str],
            "default": None,
            "description": "family level of the scientific taxonomic name",
            "prompt_hint": "Family (ShareTrait taxonomy group)",
        },
        "order_name": {
            "type": Optional[str],
            "default": None,
            "description": "order level of the scientific taxonomic name",
            "prompt_hint": "Order (ShareTrait taxonomy group)",
        },
        "class_name": {
            "type": Optional[str],
            "default": None,
            "description": "class level of the scientific taxonomic name",
            "prompt_hint": "Class (ShareTrait taxonomy group)",
        },
        "phylum_name": {
            "type": Optional[str],
            "default": None,
            "description": "phylum of the scientific taxonomic name",
            "prompt_hint": "Phylum (ShareTrait taxonomy group)",
        },
        # trait
        "trait_name": {
            "type": str,
            "default": ...,
            "description": "defined trait for the trait entity",
            "prompt_hint": "Trait name (ShareTrait trait group)",
        },
        "trait_type": {
            "type": str,
            "default": ...,
            "description": "generic measure check type for trait name to make reference to sharetrait dataset trait name. For now the possible types are {development, fecundity, metabolic_rate}",
            "prompt_hint": "Trait type (ShareTrait trait group)",
        },
        "trait_value": {
            "type": float,
            "default": ...,
            "description": "measured value of the sharetrait trait",
            "prompt_hint": "Trait value (ShareTrait trait group)",
        },
        "trait_unit": {
            "type": str,
            "default": ...,
            "description": "unit of measurement used for the trait",
            "prompt_hint": "Trait unit (ShareTrait trait group)",
        },
        # measurement
        "sample_size": {
            "type": Optional[str],
            "default": None,
            "description": "number of individuals tested for trait estimate",
            "prompt_hint": "Sample size (ShareTrait measurement group)",
        },
        "trait_error_estimate": {
            "type": Optional[str],
            "default": None,
            "description": "error associated with trait estimate",
            "prompt_hint": "Error estimate (ShareTrait measurement group)",
        },
        "trait_error_type": {
            "type": Optional[str],
            "default": None,
            "description": "type of metric used to estimate error of the trait",
            "prompt_hint": "Error type (ShareTrait measurement group)",
        },
        "trait_converted": {
            "type": Optional[str],
            "default": None,
            "description": "trait measurement value that is converted for standardisation and data integration purposes",
            "prompt_hint": "Converted value (ShareTrait measurement group)",
        },
        "measure_date": {
            "type": str,
            "default": ...,
            "description": "standard date when the measurement is being calculated.",
            "prompt_hint": "Measurement date (ShareTrait measurement group)",
        },
        # condition
        "temperature": {
            "type": Optional[float],
            "default": None,
            "description": "temperature value of the condition",
            "prompt_hint": "Temperature condition (ShareTrait condition group)",
        },
        "photoperiod": {
            "type": Optional[str],
            "default": None,
            "description": "photoperiod schedule of daylight and night hours",
            "prompt_hint": "Photoperiod condition (ShareTrait condition group)",
        },
        "humidity": {
            "type": Optional[float],
            "default": None,
            "description": "humidity level measured during the condition",
            "prompt_hint": "Humidity condition (ShareTrait condition group)",
        },
        "oxygen": {
            "type": Optional[float],
            "default": None,
            "description": "oxygen level measured during the condition",
            "prompt_hint": "Oxygen condition (ShareTrait condition group)",
        },
        "carbon_dioxide": {
            "type": Optional[float],
            "default": None,
            "description": "carbon dioxide level measured during the condition",
            "prompt_hint": "Carbon dioxide condition (ShareTrait condition group)",
        },
        "salinity": {
            "type": Optional[float],
            "default": None,
            "description": "salinity level",
            "prompt_hint": "Salinity condition (ShareTrait condition group)",
        },
        "pH": {
            "type": Optional[float],
            "default": None,
            "description": "pH value of the condition",
            "prompt_hint": "pH condition (ShareTrait condition group)",
        },
        "food_type": {
            "type": Optional[str],
            "default": None,
            "description": "food type or description of the food used during the testing",
            "prompt_hint": "Food type condition (ShareTrait condition group)",
        },
        "duration": {
            "type": Optional[int],
            "default": None,
            "description": "duration period in days of the condition",
            "prompt_hint": "Duration condition (ShareTrait condition group)",
        },
        "duration_generation": {
            "type": Optional[str],
            "default": None,
            "description": "number of generations during condition",
            "prompt_hint": "Generation duration condition (ShareTrait condition group)",
        },
        # life_stage
        "life_stage_general": {
            "type": Optional[str],
            "default": None,
            "description": "life stage used during the experiment as defined by the researcher in the paper",
            "prompt_hint": "General life stage (ShareTrait life_stage group)",
        },
        "life_stage_specific": {
            "type": Optional[str],
            "default": None,
            "description": "specific life stage used during the experiment as defined by the researcher in the paper",
            "prompt_hint": "Specific life stage (ShareTrait life_stage group)",
        },
        "lifestage_specific_initial": {
            "type": Optional[str],
            "default": None,
            "description": "specific initial life stage used at the start of the trial as defined by the researcher in the paper.",
            "prompt_hint": "Initial specific life stage (ShareTrait life_stage group)",
        },
        "lifestage_specific_final": {
            "type": Optional[str],
            "default": None,
            "description": "specific life stage used at the end of the trial as defined by the researcher in the paper",
            "prompt_hint": "Final specific life stage (ShareTrait life_stage group)",
        },
        "representative_stage": {
            "type": str,
            "default": ...,
            "description": "the current life stage if identified when an individual is being observed. Specify if it is a egg, larvae, adult stage when individual is collected",
            "prompt_hint": "Representative life stage (ShareTrait life_stage group)",
        },
        # unit_of_observation
        "population_label": {
            "type": str,
            "default": ...,
            "description": "defines if population reference provides measurements that are derived from a collection rather than individuals",
            "prompt_hint": "Unit of observation: individual vs population (values ind_measure / pop_measure)",
        },
    },
}


def _to_model_name(standard_name: str) -> str:
    return "".join(part.capitalize() for part in standard_name.split("_")) + "Metadata"


def _format_type_name(annotation: Any) -> str:
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def _build_standard_docstring(
    standard_name: str,
    field_spec: Dict[str, Dict[str, Any]],
) -> str:
    lines = [
        f"Structured metadata schema for ``{standard_name}``.",
        "",
        "Fields",
        "------",
    ]
    for field_name, spec in field_spec.items():
        required = "required" if spec["default"] is ... else "optional"
        lines.extend(
            [
                f"{field_name} : {_format_type_name(spec['type'])}",
                f"    {spec['description']} ({required}).",
            ]
        )
    return "\n".join(lines)


def _build_schema_for_standard(
    standard_name: str,
    field_spec: Dict[str, Dict[str, Any]],
) -> type[BaseModel]:
    model_fields: Dict[str, Any] = {}
    for field_name, spec in field_spec.items():
        model_fields[field_name] = (
            spec["type"],
            Field(default=spec["default"], description=spec["description"]),
        )
    return create_model(
        _to_model_name(standard_name),
        __doc__=_build_standard_docstring(standard_name, field_spec),
        __module__=__name__,
        **model_fields,
    )


def _build_prompt_template(field_spec: Dict[str, Dict[str, Any]]) -> str:
    lines = ["{"]
    entries = list(field_spec.items())
    for index, (field_name, spec) in enumerate(entries):
        prompt_hint = spec.get("prompt_hint", "...")
        comma = "," if index < len(entries) - 1 else ""
        lines.append(f'    "{field_name}": "{prompt_hint}"{comma}')
    lines.append("}")
    return "\n".join(lines)


# =============================================================================
# SCHEMA REGISTRY - Maps standard names to Pydantic models
# =============================================================================

METADATA_SCHEMAS: Dict[str, type[BaseModel]] = {
    standard_name: _build_schema_for_standard(standard_name, field_spec)
    for standard_name, field_spec in STANDARD_DEFINITIONS.items()
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
    standard_name: _build_prompt_template(field_spec)
    for standard_name, field_spec in STANDARD_DEFINITIONS.items()
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
