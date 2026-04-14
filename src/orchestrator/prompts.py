"""
This file stores all prompt templates for the multi-agent system.
"""
from langchain_core.prompts import ChatPromptTemplate


def get_planning_prompt() -> ChatPromptTemplate:
    """
    Returns the ChatPromptTemplate for the planning orchestrator.
    """
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are an expert data analysis agent that functions as a dataflow orchestrator. 
Your goal is to generate a step-by-step plan to extract metadata from a resource.

**Key Instructions:**
1.  **Be CONCISE**: Create the MINIMUM number of steps needed. Combine related analyses into single steps.
2.  **Declare Data Dependencies**: Each step must declare its `inputs` and `outputs`.
    -   `inputs`: A dictionary mapping a task's required parameters to the names of artifacts created by previous steps. If a step needs no input from the workspace, this should be an empty dictionary.
    -   `outputs`: A list of new, unique artifact names that the step will create in the workspace.
3.  **Use Available Players**: You can only assign tasks to players from the provided list.
4.  **Provide Rationale**: Briefly explain the purpose of each step in the `rationale` field.

**Metadata Standard to Adhere To:**
```
{metadata_standard}
```

**CRITICAL - Plan Efficiency Guidelines:**
- **DO NOT** create a separate step for each metadata field - combine related fields!
- For simple standards (≤5 fields): Use 2-3 steps maximum (1 analysis step + 1 generation step)
- For medium standards (6-10 fields): Use 3-4 steps maximum
- For complex standards (>10 fields): Group related fields and use 4-6 steps maximum

**MANDATORY - FINAL STEP Requirements:**
The last step MUST:
1. Use the `metadata_generator` player
2. Include `"metadata_standard": "metadata_standard"` in its `inputs` dictionary (THIS IS REQUIRED!)
3. Set `outputs` to exactly `["metadata_output"]` (THIS IS REQUIRED!)
4. Include all relevant artifacts from previous steps in `inputs`
5. Generate concrete values for each metadata field

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
                """Generate a CONCISE metadata extraction plan for a resource of type: '{file_type}'.

REQUIREMENTS:
1. Use MINIMUM steps - combine related analyses
2. FINAL STEP must use `metadata_generator` player
3. FINAL STEP inputs MUST include: {{"metadata_standard": "metadata_standard"}}
4. FINAL STEP outputs MUST be exactly: ["metadata_output"]

Keep the plan SHORT.""",
            ),
        ]
    )


def get_multi_resource_planning_prompt() -> ChatPromptTemplate:
    """
    Returns the ChatPromptTemplate for planning multi-resource context analysis.
    """
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are an expert data analysis agent that functions as a dataflow orchestrator for MULTI-RESOURCE CONTEXTS.
Your goal is to generate a step-by-step plan to extract metadata from a context consisting of MULTIPLE related resources.

**Context Overview:**
{dataset_info}

**Key Instructions for Multi-Resource Analysis:**

1.  **Be CONCISE**: Create the MINIMUM number of steps. Combine analyses where possible.
2.  **Phase 1 - Resource Analysis**: Analyze resources (can combine multiple resources in one step if similar analysis needed)
3.  **Phase 2 - Relationship Discovery**: One step to discover relationships between resources
4.  **Phase 3 - Final Generation**: Use `metadata_generator` to produce all metadata values

**Step Schema**: Each step must include:
- `task`: The specific task to perform
- `player`: The player role to execute this task
- `rationale`: Why this step is needed
- `target_resources`: List of resource names this step operates on (empty list = context-level operation)
- `inputs`: Dictionary mapping parameters to artifacts from previous steps
- `outputs`: List of artifact names this step produces

**Metadata Standard:**
```
{metadata_standard}
```

**CRITICAL - Plan Efficiency Guidelines:**
- **DO NOT** create a separate step for each resource or each field - combine!
- For 2-3 resources: Use 3-4 steps (1 combined analysis + 1 relationship + 1 generation)
- For 4+ resources: Use 4-6 steps maximum

**MANDATORY - FINAL STEP Requirements:**
The last step MUST:
1. Use the `metadata_generator` player
2. Include `"metadata_standard": "metadata_standard"` in its `inputs` dictionary (THIS IS REQUIRED!)
3. Set `outputs` to exactly `["metadata_output"]` (THIS IS REQUIRED!)
4. Include all relevant artifacts from previous steps in `inputs`
5. Generate concrete values for each metadata field

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
3. FINAL STEP must use `metadata_generator` player
4. FINAL STEP inputs MUST include: {{"metadata_standard": "metadata_standard"}}
5. FINAL STEP outputs MUST be exactly: ["metadata_output"]

Keep the plan SHORT (3-5 steps).""",
            ),
        ]
    )


def get_task_execution_prompt() -> ChatPromptTemplate:
    """
    Returns the prompt template for task execution by a player.
    """
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

**Target Resources for This Step:** {target_tables}

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
    """
    Returns the prompt for generating initial work in a debate.
    """
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

**Target Resources:** {target_tables}

**Available Context:**
{context}

Provide your initial analysis.
""",
            ),
        ]
    )


def get_critique_prompt() -> ChatPromptTemplate:
    """
    Returns the prompt for critiquing other players' work.
    """
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
    """
    Returns the prompt for revising work based on critiques.
    """
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
    """
    Returns the prompt for synthesizing multiple analyses into one.
    """
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
    """
    DEPRECATED: Use get_multi_resource_planning_prompt instead.
    """
    return get_multi_resource_planning_prompt()