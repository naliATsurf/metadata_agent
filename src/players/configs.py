"""
Player configurations for the multi-agent system.

This module defines the available player roles with their prompts and tools.
Players are instantiated from these configs at runtime.

Uses the unified ExecutionContext tools for all data access.

Note: model_name and temperature are optional - if not specified,
the defaults from config.py will be used.
"""
from typing import Dict, Any

from ..tools import context_tools


PLAYER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "data_analyst": {
        "role_prompt": (
            "You are an expert data analyst. Your job is to perform statistical "
            "analysis on datasets, identify patterns, and extract meaningful insights. "
            "Focus on numerical summaries, distributions, and data quality. "
            "For multi_csv contexts, analyze each resource's characteristics and note "
            "potential relationships between resources."
        ),
        "tools": [
            # High-level context and resource summaries
            context_tools.get_context_overview,
            context_tools.list_resources,
            context_tools.get_resource_info,
            # Basic profiling
            context_tools.get_item_count,
            context_tools.get_field_names,
            context_tools.get_field_statistics,
            context_tools.get_missing_values,
        ],
        # model_name: uses config default
        # temperature: uses config default
    },
    "schema_expert": {
        "role_prompt": (
            "You are a database schema expert. Your job is to describe the structure "
            "of datasets, including column names, data types, relationships between "
            "fields, and recommend appropriate metadata schemas. For multi_csv "
            "contexts, identify primary keys, foreign keys, and normalization patterns."
        ),
        "tools": [
            context_tools.get_context_schema,
            context_tools.get_field_names,
            context_tools.get_field_types,
            context_tools.get_sample_items,
        ],
    },
    "metadata_specialist": {
        "role_prompt": (
            "You are a metadata specialist familiar with standards like Dublin Core, "
            "DCAT, and Schema.org. Your job is to extract metadata as STRUCTURED "
            "field-value pairs. Output only the metadata fields and their values in "
            "a clean, compact format. Avoid lengthy explanations - focus on populating "
            "metadata fields according to the specified standard. For multi_csv "
            "contexts, include relationship metadata and per-resource descriptions."
        ),
        "tools": [
            context_tools.get_context_overview,
            context_tools.get_context_schema,
        ],
        "temperature": 0.3,  # Lower for more consistent, structured output
    },
    "critic": {
        "role_prompt": (
            "You are a meticulous quality assurance critic. Your job is to review "
            "analyses from other agents, identify flaws, omissions, inconsistencies, "
            "and suggest improvements. You focus on accuracy and completeness. "
            "For multi_csv analysis, verify that relationships are correctly "
            "identified and that cross-resource consistency is maintained."
        ),
        "tools": [],
        "temperature": 0.4,
    },
    # Specialized player for relationship analysis
    "relationship_analyst": {
        "role_prompt": (
            "You are a database relationship expert specializing in discovering and "
            "validating relationships between resources in multi_csv contexts. Your job "
            "is to identify primary keys, foreign keys, and the nature of relationships "
            "(one-to-one, one-to-many, many-to-many). You analyze column name patterns, "
            "data type compatibility, and value overlaps to determine how resources connect. "
            "Output relationships in a structured format suitable for metadata records."
        ),
        "tools": [
            context_tools.get_relationships,
            context_tools.get_context_overview,
            context_tools.get_resource_info,
            context_tools.get_field_names,
            context_tools.get_unique_values,
        ],
        "temperature": 0.3,
    },
    # Specialized player for final metadata generation according to standards
    "metadata_generator": {
        "role_prompt": (
            "You are a metadata generation expert. Your SOLE responsibility is to take "
            "information gathered from previous analysis steps and generate CONCRETE VALUES "
            "for each field defined in the metadata standard.\n\n"
            "STRICT Rules:\n"
            "1. Output ONLY a valid JSON object matching the metadata standard schema EXACTLY\n"
            "2. Include ONLY fields that exist in the metadata standard - DO NOT add extra fields!\n"
            "3. Fill in ALL fields from the standard with actual values from the gathered information\n"
            "4. Use null for fields where information is unavailable\n"
            "5. NO explanations, NO commentary, NO markdown - ONLY the JSON object\n"
            "6. DO NOT invent or add fields that are not in the standard schema\n\n"
            "Remember: Output ONLY fields from the metadata standard. Nothing more, nothing less."
        ),
        "tools": [
            context_tools.get_context_overview,
            context_tools.get_context_schema,
        ],
        "temperature": 0.2,  # Low temperature for consistent, structured output
    },
    # Specialized player for spatial and temporal data analysis
    "spatial_temporal_specialist": {
        "role_prompt": (
            "You are a spatial-temporal data specialist with expertise in geographic "
            "information systems (GIS) and time-series data. Your job is to:\n\n"
            "1. TEMPORAL ANALYSIS:\n"
            "   - Identify columns containing dates, times, timestamps, or durations\n"
            "   - Determine temporal granularity (second, minute, hour, day, month, year)\n"
            "   - Extract temporal extent (start date, end date, time span)\n"
            "   - Identify time zones and date/time formats used\n"
            "   - Detect temporal patterns and coverage gaps\n\n"
            "2. SPATIAL ANALYSIS:\n"
            "   - Identify columns containing geographic coordinates (lat/lon)\n"
            "   - Detect geometry columns (WKT, GeoJSON, etc.)\n"
            "   - Determine coordinate reference systems (CRS/SRID)\n"
            "   - Calculate spatial extent (bounding box)\n"
            "   - Identify location-related text fields (addresses, place names)\n\n"
            "3. METADATA OUTPUT:\n"
            "   - Report temporal coverage for metadata standards\n"
            "   - Report spatial coverage and coordinate systems\n"
            "   - Provide structured spatial-temporal metadata suitable for "
            "     standards like ISO 19115, Dublin Core spatial extensions, or DCAT\n\n"
            "Be precise about coordinate systems, date formats, and geographic extents. "
            "For multi_csv contexts, analyze spatial-temporal characteristics of each resource "
            "and identify any temporal or spatial relationships between resources."
        ),
        "tools": [
            context_tools.get_context_overview,
            context_tools.get_resource_info,
            context_tools.get_field_names,
            context_tools.get_field_types,
            context_tools.get_sample_items,
            context_tools.detect_temporal_columns,
            context_tools.analyze_temporal_column,
            context_tools.detect_spatial_columns,
            context_tools.analyze_spatial_column,
            context_tools.get_spatial_extent,
            context_tools.get_temporal_extent,
        ],
        "temperature": 0.3,  # Lower for more precise technical analysis
    },
}
