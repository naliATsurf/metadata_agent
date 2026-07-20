"""
This file stores all prompt templates for the multi-agent system.
"""
from langchain_core.prompts import ChatPromptTemplate


def get_single_csv_planning_prompt() -> ChatPromptTemplate:
    '''Returns the ChatPromptTemplate for the planning orchestrator.

    System prompt:
        You are an expert data analysis agent that functions as a dataflow orchestrator.
        Your goal is to generate a step-by-step plan to extract metadata from a resource.

        **Key Instructions:**

        1.  **Be CONCISE**: Create the MINIMUM number of steps needed. Combine related analyses into single steps.
        2.  **Declare Data Dependencies**: Each step must declare its ``inputs`` and ``outputs``.

            -   ``inputs``: A dictionary mapping a task's required parameters to the names of artifacts created by previous steps. If a step needs no input from the workspace, this should be an empty dictionary.
            -   ``outputs``: A list of new, unique artifact names that the step will create in the workspace.
        3.  **Use Available Players**: You can only assign tasks to players from the provided list.
        4.  **Provide Rationale**: Briefly explain the purpose of each step in the ``rationale`` field.

        **Metadata Standard to Adhere To:**
        ``{metadata_standard}``

        **CRITICAL - Plan Efficiency Guidelines:**

        - **DO NOT** create a separate step for each metadata field - combine related fields!
        - For simple standards (≤5 fields): Use 2-3 steps maximum (1 analysis step + 1 generation step)
        - For medium standards (6-10 fields): Use 3-4 steps maximum
        - For complex standards (>10 fields): Group related fields and use 4-6 steps maximum

        **CRITICAL - Data Profiling Requirement (Even for Small Datasets):**
        
        - At least one step BEFORE the final generation MUST be executed by ``data_analyst`` and MUST run ``get_field_statistics`` (optionally also ``get_missing_values``) so numeric ranges and distributions are available for metadata values.
        - If the context has multiple resources, run ``get_field_statistics`` for each resource (or a combined step that still produces per-resource ``field_stats`` artifacts).

        **MANDATORY - FINAL STEP Requirements:**
        The last step MUST:
        
        1. Use the ``metadata_generator`` player
        2. Include ``"metadata_standard": "metadata_standard"`` in its ``inputs`` dictionary (THIS IS REQUIRED!)
        3. Set ``outputs`` to exactly ``["metadata_output"]`` (THIS IS REQUIRED!)
        4. Include all relevant artifacts from previous steps in ``inputs``
        5. Generate concrete values for each metadata field

        Example final step inputs format:

        ``"inputs": {{"metadata_standard": "metadata_standard", "analysis": "analysis_artifact", ...}}``

        **Available Players:**
        ``{available_players}``

        **OUTPUT FORMAT (CRITICAL)**:
        You MUST output **ONLY** a JSON object that conforms to the following schema:
        ``{format_instructions}``

    Human prompt:
        Generate a CONCISE metadata extraction plan for a resource of type: ``{file_type}``.

        REQUIREMENTS:

        1. Use MINIMUM steps - combine related analyses
        2. FINAL STEP must use ``metadata_generator`` player
        3. FINAL STEP inputs MUST include: ``{{"metadata_standard": "metadata_standard"}}``
        4. FINAL STEP outputs MUST be exactly: ``["metadata_output"]``
        5. Even for small datasets, include a ``data_analyst`` step that runs ``get_field_statistics`` (and optionally ``get_missing_values``) before the final step.

        Keep the plan SHORT.
    '''
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are an expert data analysis agent that functions as a dataflow orchestrator.
Your goal is to generate a step-by-step plan to extract metadata from a resource.

**Context Overview (the actual data you are planning for):**
{dataset_info}

**Grounding rules (CRITICAL — read before planning):**
- Use the **exact resource name(s)** from the Context Overview in `target_resources`. Never use the file type (e.g. "SINGLE_CSV") as a resource name.
- **Never invent column names.** Refer only to columns shown in the Context Overview and Data profile. Do not assume a column exists because the standard mentions a concept.
- `inputs` is **only** for workspace artifacts produced by previous steps. Never put tool arguments (such as a column name) in `inputs`.
- Do not name specific columns or pick tools for a step. Which columns a step works on is discovered at execution time by the assigned player's own tools; your job is to choose steps and players, not their arguments.

**Key Instructions:**
1.  **Be CONCISE**: Create the MINIMUM number of steps needed. Combine related analyses into single steps.
2.  **Declare Data Dependencies**: Each step must declare its `inputs` and `outputs`.
    -   `inputs`: A dictionary mapping a task's required parameters to the names of artifacts created by previous steps. If a step needs no input from the workspace, this should be an empty dictionary.
    -   `outputs`: A list of new, unique artifact names that the step will create in the workspace.
    -   **One artifact per `inputs` value**: Each value must be exactly **one** workspace artifact name. Never use comma-separated lists in a single value—the runtime treats the entire string as one artifact id and does not split on commas. If several prior artifacts are needed, use **multiple** parameter keys (one per artifact) or add an earlier step whose **single** `outputs` entry bundles them under one new name, then reference only that name.
3.  **Use Available Players**: You can only assign tasks to players from the provided list.
4.  **Provide Rationale**: Briefly explain the purpose of each step in the `rationale` field.
5.  **Match steps to what the data supports**: The Metadata Standard defines the fields to fill; the Data profile shows what the data actually contains. Add an analysis step (with the appropriate specialist player) to gather a field's information **only when the profile shows the data supports it**. If the standard asks for something the profile shows the data lacks, do not add a step for it — leave that field for the generator to set to null.

**Metadata Standard to Adhere To:**
```
{metadata_standard}
```

**CRITICAL - Plan Efficiency Guidelines:**
- **DO NOT** create a separate step for each metadata field - combine related fields!
- For simple standards (≤5 fields): Use 2-3 steps maximum (1 analysis step + 1 generation step)
- For medium standards (6-10 fields): Use 3-4 steps maximum
- For complex standards (>10 fields): Group related fields and use 4-6 steps maximum
- Add a specialist analysis step only when the standard needs information a specialist player can extract **and** the Data profile shows the data supports it. When no such step is warranted, keep the plan to profiling + generation.

**CRITICAL - Data Profiling Requirement (Even for Small Datasets):**
- At least one step BEFORE the final generation MUST be executed by `data_analyst` and MUST run `get_field_statistics` (optionally also `get_missing_values`) so numeric ranges and distributions are available for metadata values.
- If the context has multiple resources, run `get_field_statistics` for each resource (or a combined step that still produces per-resource `field_stats` artifacts).
- The Data profile is authoritative about what the data contains; the standard's wording is only a statement of *desire*. Add analysis steps only for information the profile shows the data actually supports, and leave unsupportable fields for the generator to set to null.
- Give analysis steps an empty `inputs` and let the assigned player discover the relevant columns at execution; never name columns or choose tools in the plan.

**MANDATORY - FINAL STEP Requirements:**
The last step MUST:
1. Use the `metadata_generator` player
2. Include `"metadata_standard": "metadata_standard"` in its `inputs` dictionary (THIS IS REQUIRED!)
3. Set `outputs` to exactly `["metadata_output"]` (THIS IS REQUIRED!)
4. Include all relevant artifacts from previous steps in `inputs`
5. Generate concrete values for each metadata field

**WRONG** (invalid—comma list in one value):
```json
"inputs": {{"metadata_standard": "metadata_standard", "stats": "stats_a,stats_b"}}
```

**RIGHT** (one artifact name per value):
```json
"inputs": {{"metadata_standard": "metadata_standard", "first_stats": "stats_a", "second_stats": "stats_b"}}
```

Example final step inputs format:
```json
"inputs": {{"metadata_standard": "metadata_standard", "analysis": "analysis_artifact", ...}}
```

**Available Players:** 
{available_players}

**OUTPUT FORMAT (CRITICAL)**:
You MUST output **ONLY** a JSON object that conforms to the following schema:

{format_instructions}
""",
            ),
            (
                "human",
                """Generate a CONCISE metadata extraction plan for the data described in the Context Overview above (type: '{file_type}').

REQUIREMENTS:

1. Use MINIMUM steps - combine related analyses
2. FINAL STEP must use ``metadata_generator`` player
3. FINAL STEP inputs MUST include: ``{{"metadata_standard": "metadata_standard"}}``
4. FINAL STEP outputs MUST be exactly: ``["metadata_output"]``
5. Even for small datasets, include a ``data_analyst`` step that runs ``get_field_statistics`` (and optionally ``get_missing_values``) before the final step.
6. Use the exact resource and column names from the Context Overview. Do NOT invent columns, and do NOT put column names in ``inputs``.

Keep the plan SHORT.""",
            ),
        ]
    )


def get_planning_prompt() -> ChatPromptTemplate:
    '''DEPRECATED: Use :func:`get_single_csv_planning_prompt` instead.

    Original prompt content:
        This function delegates to get_single_csv_planning_prompt().
    '''
    return get_single_csv_planning_prompt()

def get_multi_csv_planning_prompt() -> ChatPromptTemplate:
    '''Returns the ChatPromptTemplate for planning multi_csv context analysis.

    System prompt:
        You are an expert data analysis agent that functions as a dataflow orchestrator for MULTI-RESOURCE CONTEXTS.
        Your goal is to generate a step-by-step plan to extract metadata from a context consisting of MULTIPLE related resources.

        **Context Overview:**
        ``{dataset_info}``

        **Key Instructions for Multi-CSV Analysis:**

        1.  **Be CONCISE**: Create the MINIMUM number of steps. Combine analyses where possible.
        2.  **Phase 1 - Resource Analysis**: Analyze resources (can combine multiple resources in one step if similar analysis needed)
        3.  **Phase 2 - Relationship Discovery**: One step to discover relationships between resources
        4.  **Phase 3 - Final Generation**: Use ``metadata_generator`` to produce all metadata values

        **CRITICAL - Data Profiling Requirement (Even for Small Datasets):**
        
        - Phase 1 MUST include a ``data_analyst`` field profiling step using ``get_field_statistics`` (optionally ``get_missing_values``) so numeric ranges/distributions are available for downstream metadata.
        - For multi_csv contexts, ensure ``get_field_statistics`` is executed for each resource (or produces per-resource ``field_stats`` artifacts).

        **Step Schema**: Each step must include:

        - ``task``: The specific task to perform
        - ``player``: The player role to execute this task
        - ``rationale``: Why this step is needed
        - ``target_resources``: List of resource names this step operates on (empty list = context-level operation)
        - ``inputs``: Dictionary mapping parameters to artifacts from previous steps
        - ``outputs``: List of artifact names this step produces

        **Metadata Standard:**
        ``{metadata_standard}``

        **CRITICAL - Plan Efficiency Guidelines:**

        - **DO NOT** create a separate step for each resource or each field - combine!
        - For 2-3 resources: Use 3-4 steps (1 combined analysis + 1 relationship + 1 generation)
        - For 4+ resources: Use 4-6 steps maximum

        **MANDATORY - FINAL STEP Requirements:**
        The last step MUST:
        
        1. Use the ``metadata_generator`` player
        2. Include ``"metadata_standard": "metadata_standard"`` in its ``inputs`` dictionary (THIS IS REQUIRED!)
        3. Set ``outputs`` to exactly ``["metadata_output"]`` (THIS IS REQUIRED!)
        4. Include all relevant artifacts from previous steps in ``inputs``
        5. Generate concrete values for each metadata field

        Example final step inputs format:

        ``"inputs": {{"metadata_standard": "metadata_standard", "context_overview": "context_overview", ...}}``

        **Available Players:**
        ``{available_players}``

        **OUTPUT FORMAT (CRITICAL)**:
        You MUST output **ONLY** a JSON object that conforms to the following schema:
        ``{format_instructions}``

        **Important Notes:**

        - Use the exact resource names provided in the context overview
        - Namespace artifacts by resource name using colon notation: "resourcename:artifact"
        - For cross-resource or context-level operations, use empty ``target_resources`` list
        - Ensure relationship discovery happens AFTER individual resource analysis

    Human prompt:
        Generate a CONCISE metadata extraction plan for context ``{dataset_name}``.

        Resources: ``{table_names}``
        File type: ``{file_type}``

        REQUIREMENTS:

        1. Use MINIMUM steps - combine resource analyses
        2. Include ONE relationship discovery step
        3. Include at least one ``data_analyst`` profiling step using ``get_field_statistics`` (and optionally ``get_missing_values``) even if the dataset is small.
        4. FINAL STEP must use ``metadata_generator`` player
        5. FINAL STEP inputs MUST include: ``{{"metadata_standard": "metadata_standard"}}``
        6. FINAL STEP outputs MUST be exactly: ``["metadata_output"]``

        Keep the plan SHORT (3-5 steps).
    '''
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are an expert data analysis agent that functions as a dataflow orchestrator for MULTI-RESOURCE CONTEXTS.
Your goal is to generate a step-by-step plan to extract metadata from a context consisting of MULTIPLE related resources.

**Context Overview:**
{dataset_info}

**Key Instructions for Multi-CSV Analysis:**

1.  **Be CONCISE**: Create the MINIMUM number of steps. Combine analyses where possible.
2.  **Phase 1 - Resource Analysis**: Analyze resources (can combine multiple resources in one step if similar analysis needed)
3.  **Phase 2 - Relationship Discovery**: One step to discover relationships between resources
4.  **Phase 3 (Conditional) - Spatial Analysis**: If the metadata standard contains spatial/geospatial requirements, add a `spatial_temporal_specialist` step before final generation.
5.  **Phase 4 - Final Generation**: Use `metadata_generator` to produce all metadata values

**CRITICAL - Data Profiling Requirement (Even for Small Datasets):**
- Phase 1 MUST include a `data_analyst` field profiling step using `get_field_statistics` (optionally `get_missing_values`) so numeric ranges/distributions are available for downstream metadata.
- For multi_csv contexts, ensure `get_field_statistics` is executed for each resource (or produces per-resource `field_stats` artifacts).
- If **Metadata Standard** indicates spatial/geospatial requirements, you MUST include a `spatial_temporal_specialist` step after relationship discovery and before final metadata generation.
- Infer this from the text of `{metadata_standard}` (for example: spatial, geospatial, geometry, coordinate, latitude, longitude, bbox/bounding box, CRS, extent, coverage). Do not rely on standard name matching.
- If any resource has coordinates as a single column of ``(lon, lat)`` or ``(lat, lon)`` tuples (e.g. ``tuple_coords``), the `spatial_temporal_specialist` should use ``get_spatial_extent_from_tuple_column`` for extent; use ``get_spatial_extent`` only for separate numeric lat/lon columns.

**Step Schema**: Each step must include:
- `task`: The specific task to perform
- `player`: The player role to execute this task
- `rationale`: Why this step is needed
- `target_resources`: List of resource names this step operates on (empty list = context-level operation)
- `inputs`: Dictionary mapping parameters to artifacts from previous steps (each value must be **one** artifact name only—no comma-separated lists; use multiple keys or one prior step that outputs a single combined artifact)
- `outputs`: List of artifact names this step produces

**Metadata Standard:**
```
{metadata_standard}
```

**CRITICAL - Plan Efficiency Guidelines:**
- **DO NOT** create a separate step for each resource or each field - combine!
- For 2-3 resources: Use 3-4 steps (1 combined analysis + 1 relationship + 1 generation)
- For 4+ resources: Use 4-6 steps maximum
- If spatial/geospatial requirements are detected in `{metadata_standard}`, the plan SHOULD be exactly 4 steps: (1) resource profiling, (2) relationship discovery, (3) `spatial_temporal_specialist`, (4) final `metadata_generator`.

**MANDATORY - FINAL STEP Requirements:**
The last step MUST:
1. Use the `metadata_generator` player
2. Include `"metadata_standard": "metadata_standard"` in its `inputs` dictionary (THIS IS REQUIRED!)
3. Set `outputs` to exactly `["metadata_output"]` (THIS IS REQUIRED!)
4. Include all relevant artifacts from previous steps in `inputs`
5. Generate concrete values for each metadata field

**WRONG** (invalid—comma list in one value):
```json
"inputs": {{"metadata_standard": "metadata_standard", "stats": "event:stats,occurrence:stats,temp:stats"}}
```

**RIGHT** (one artifact per parameter):
```json
"inputs": {{"metadata_standard": "metadata_standard", "event_stats": "event:stats", "occurrence_stats": "occurrence:stats", "temp_stats": "temp:stats"}}
```

Example final step inputs format:
```json
"inputs": {{"metadata_standard": "metadata_standard", "context_overview": "context_overview", ...}}
```

**Available Players:** 
{available_players}

**OUTPUT FORMAT (CRITICAL)**:
You MUST output **ONLY** a JSON object that conforms to the following schema:

{format_instructions}

**Important Notes:**
- Use the exact resource names provided in the context overview
- Namespace artifacts by resource name using colon notation: "resourcename:artifact"
- Each `inputs` map value must be a **single** artifact name (never `"a,b,c"` in one string); use separate keys or one merged artifact from a prior step
- For cross-resource or context-level operations, use empty `target_resources` list
- Ensure relationship discovery happens AFTER individual resource analysis
""",
            ),
            (
                "human",
                """Generate a CONCISE metadata extraction plan for context '{dataset_name}'.

Resources: {table_names}
File type: {file_type}

REQUIREMENTS:
1. Use MINIMUM steps - combine resource analyses
2. Include ONE relationship discovery step  
3. Include at least one `data_analyst` profiling step using `get_field_statistics` (and optionally `get_missing_values`) even if the dataset is small.
4. If `{metadata_standard}` includes spatial/geospatial concepts (e.g., spatial/geospatial, coordinate, latitude/longitude, extent/coverage, CRS), include one `spatial_temporal_specialist` step before final generation.
5. For spatial/geospatial standards, produce exactly 4 steps.
6. FINAL STEP must use `metadata_generator` player
7. FINAL STEP inputs MUST include: {{"metadata_standard": "metadata_standard"}}
8. FINAL STEP outputs MUST be exactly: ["metadata_output"]

Keep the plan SHORT (3-5 steps).""",
            ),
        ]
    )


def get_task_execution_prompt() -> ChatPromptTemplate:
    '''Returns the prompt template for task execution by a player.

    System prompt:
        You are ``{player_name}.{role_prompt}``

        You are executing a specific task as part of a metadata extraction workflow.
        Your goal is to complete the task thoroughly and provide actionable results.

        **Available Tools:**
        ``{tool_descriptions}``

        **Metadata Standard to Follow:**
        ``{metadata_standard}``

    Human prompt:
        **Task:** ``{task}``

        **Context Information:**
        ``{dataset_info}``

        **Target Resources for This Step:** ``{target_resources}``

        **Context from Previous Steps:**
        ``{input_context}``

        **Tool Results:**
        ``{tool_results}``

        Execute this task and provide a comprehensive response.
    '''
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are {player_name}. {role_prompt}

You are executing a specific task as part of a metadata extraction workflow.
Your goal is to complete the task thoroughly and provide actionable results.

**Available Tools:**
{tool_descriptions}

**Metadata Standard to Follow:**
{metadata_standard}
""",
            ),
            (
                "human",
                """**Task:** {task}

**Context Information:**
{dataset_info}

**Target Resources for This Step:** {target_resources}

**Context from Previous Steps:**
{input_context}

**Tool Results:**
{tool_results}

Execute this task and provide a comprehensive response.
""",
            ),
        ]
    )
    
def get_initial_work_prompt() -> ChatPromptTemplate:
    '''Returns the prompt for generating initial work in a debate.

    System prompt:
        You are ``{player_name}.{role_prompt}``

        You are participating in a multi-agent analysis debate. Your goal is to provide
        your unique perspective and insights based on your expertise.

    Human prompt:
        **Task:** ``{task}``

        **Context Information:**
        ``{dataset_info}``

        **Target Resources:** ``{target_resources}``

        **Available Context:**
        ``{context}``

        Provide your initial analysis.
    '''
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are {player_name}. {role_prompt}

You are participating in a multi-agent analysis debate. Your goal is to provide
your unique perspective and insights based on your expertise.
""",
            ),
            (
                "human",
                """**Task:** {task}

**Context Information:**
{dataset_info}

**Target Resources:** {target_resources}

**Available Context:**
{context}

Provide your initial analysis.
""",
            ),
        ]
    )


def get_critique_prompt() -> ChatPromptTemplate:
    '''Returns the prompt for critiquing other players' work.

    System prompt:
        You are ``{player_name}.{role_prompt}``

    Human prompt:
        **Task being analyzed:** ``{task}``

        **Work from other players to critique:**

        ``{other_work}``

        Provide your detailed critique.
    '''
    return ChatPromptTemplate.from_messages(
        [
            ("system", "You are {player_name}. {role_prompt}"),
            (
                "human",
                """**Task being analyzed:** {task}

**Work from other players to critique:**

{other_work}

Provide your detailed critique.
""",
            ),
        ]
    )


def get_revision_prompt() -> ChatPromptTemplate:
    '''Returns the prompt for revising work based on critiques.

    System prompt:
        You are ``{player_name}.{role_prompt}``

    Human prompt:
        **Task:** ``{task}``

        **Your Original Analysis:**
        ``{original_work}``

        **Critiques Received:**
        ``{critiques}``

        Provide your revised analysis.
    '''
    return ChatPromptTemplate.from_messages(
        [
            ("system", "You are {player_name}. {role_prompt}"),
            (
                "human",
                """**Task:** {task}

**Your Original Analysis:**
{original_work}

**Critiques Received:**
{critiques}

Provide your revised analysis.
""",
            ),
        ]
    )


def get_synthesis_prompt() -> ChatPromptTemplate:
    '''Returns the prompt for synthesizing multiple analyses into one.

    System prompt:
        You are a synthesis expert responsible for combining multiple analyses into a single, structured metadata record.

        - Be CONCISE: Output only the essential metadata fields and values
        - Be STRUCTURED: Use a clean key-value format or JSON structure
        - NO lengthy explanations or narratives
        - NO redundant information
        - Focus on FACTS, not process descriptions

    Human prompt:
        **Task that was analyzed:** ``{task}``

        **Analyses from all participants:**

        ``{all_results}``

        Produce the final metadata output as a **structured record**.
    '''
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are a synthesis expert responsible for combining multiple analyses into a single, structured metadata record.
- Be CONCISE: Output only the essential metadata fields and values
- Be STRUCTURED: Use a clean key-value format or JSON structure
- NO lengthy explanations or narratives
- NO redundant information
- Focus on FACTS, not process descriptions
""",
            ),
            (
                "human",
                """**Task that was analyzed:** {task}

**Analyses from all participants:**

{all_results}

Produce the final metadata output as a **structured record**.
""",
            ),
        ]
    )

def get_multi_table_planning_prompt() -> ChatPromptTemplate:
    '''DEPRECATED: Use :func:`get_multi_csv_planning_prompt` instead.

    Original prompt content:
        This function delegates to get_multi_csv_planning_prompt().
    '''
    return get_multi_csv_planning_prompt()
