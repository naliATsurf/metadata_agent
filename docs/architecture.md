# Metadata Agent Architecture

This document describes the architecture of the multi-agent metadata extraction system.

## Overview

The Metadata Agent is a multi-agent system that extracts metadata from datasets using:
1. **Unified ExecutionContext**: Abstract context layer that handles CSV and SQLite inputs.
2. **Planning**: An LLM generates a step-by-step plan based on the data source and metadata standard
3. **Parallel Execution**: Multiple players execute each step simultaneously
4. **Debate**: Players critique and revise each other's work to improve quality
5. **Synthesis**: A synthesizer consolidates results into a final output

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER INPUT                                         │
│     (file path, list of paths, dict, directory, SQLite, ExecutionContext)     │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ContextFactory                                        │
│            (Auto-detects type, creates appropriate ExecutionContext)         │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 ExecutionContext (Unified Interface)                         │
│                                                                              │
│                  CSVContext │ SQLiteContext (future) │ ...                  │
│                                                                              │
│  Properties: name, resources, is_multi_csv, context_type                    │
│  Methods: get_resource_info(), read_resource(), get_relationships(), ...    │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ORCHESTRATOR                                    │
│         orchestrator.run(source, metadata_standard)                         │
│         (Unified entry point for ALL data sources)                          │
│                                                                              │
│  Note: For multi_csv contexts, 'relationship_analyst' is automatically      │
│        added to the player pool.                                            │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │ Plan: [Step1, Step2, Step3, ...]
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PLAN EXECUTOR                                      │
│                    (Iterates through plan steps)                            │
│                    (Maintains workspace of artifacts)                       │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │ For each step:
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              STEP EXECUTOR (LangGraph)                                      │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  1. PARALLEL EXECUTION                                              │     │
│  │     Player1 ──┐                                                     │     │
│  │     Player2 ──┼──► Execute same task with different perspectives   │     │
│  │     Player3 ──┘                                                     │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                              │                                               │
│                              ▼                                               │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  2. DEBATE LOOP (critique → revise → repeat)                        │     │
│  │     - Each player critiques others' work                           │     │
│  │     - Each player revises based on critiques                       │     │
│  │     - Repeat for N debate rounds                                   │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                              │                                               │
│                              ▼                                               │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  3. SYNTHESIS                                                       │     │
│  │     Synthesizer consolidates all results into final answer         │     │
│  └────────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. ExecutionContext (`src/context/`)

The unified data access layer that abstracts away differences between data formats.

```python
from src.context import create_context

# All of these create appropriate ExecutionContext objects:
ctx = create_context("./data/users.csv")           # single_csv
ctx = create_context("./data/mydb.sqlite")         # sqlite
ctx = create_context("./data/my_dataset/")         # directory of CSVs
ctx = create_context(["./a.csv", "./b.csv"])       # list of files
ctx = create_context({                              # named resources
    "users": "./users.csv",
    "orders": "./orders.csv"
})
```

**ExecutionContext Interface:**
- `resources` - List of resource names
- `is_multi_csv` - Boolean indicating multiple CSV resources
- `context_type` - Type of context (`single_csv`, `multi_csv`, `sqlite`, etc.)
- `get_resource_info(resource)` - Get metadata for a resource
- `read_resource(resource)` - Read resource data as DataFrame
- `get_relationships()` - Get discovered relationships

### 2. Orchestrator (`src/orchestrator/orchestrator.py`)

The main entry point that coordinates planning and execution with a **single unified interface**.

```python
from src.orchestrator import Orchestrator, run_metadata_extraction
from src.standards import METADATA_STANDARDS

# Create orchestrator
orchestrator = Orchestrator(topology_name="default")

# Run on ANY data source - same interface for all
result = orchestrator.run(
    source="./data/users.csv",  # or dict, or list, or directory, or sqlite
    metadata_standard=METADATA_STANDARDS["basic"]
)

# Or use convenience function
result = run_metadata_extraction(
    source={"users": "./users.csv", "orders": "./orders.csv"},
    metadata_standard=METADATA_STANDARDS["relational"]
)
```

**Multi-CSV Auto-adaptation:**
The orchestrator automatically adds `relationship_analyst` to the player pool when analyzing multi_csv contexts. No separate topology needed.

### 3. Player (`src/players/player.py`)

A unified agent class that can execute tasks and participate in debates.

```python
from src.players import Player, create_player_from_config, PLAYER_CONFIGS

player = create_player_from_config(PLAYER_CONFIGS["data_analyst"], name="analyst_1")

# Execute a task with ExecutionContext
result = player.execute_task(
    task="Analyze dataset structure",
    context_key="ctx_abc123",
    context_info={...},
    workspace={},
    inputs={}
)
```

### 4. Topology & Player Configs

Configuration is split into two modules:
- **Player Configs** (`src/players/configs.py`): Defines player roles, prompts, and tools
- **Execution Topologies** (`src/topology.py`): Defines how plans are executed

#### Execution Topologies (`src/topology.py`)

```python
EXECUTION_TOPOLOGIES = {
    "default": {
        "description": "Standard execution with 3 parallel players, 2 debate rounds",
        "players_per_step": 3,
        "debate_rounds": 2,
        "player_pool": ["data_analyst", "schema_expert", "metadata_specialist"],
    },
    "fast": {
        "description": "Quick execution with 2 players and minimal debate",
        "players_per_step": 2,
        "debate_rounds": 1,
        "player_pool": ["data_analyst", "schema_expert"],
    },
    "thorough": {
        "description": "Thorough execution with more players and extended debate",
        "players_per_step": 4,
        "debate_rounds": 3,
        "player_pool": ["data_analyst", "schema_expert", "metadata_specialist", "critic"],
    },
    "single": {
        "description": "Single player execution with no debate. Fastest but least robust.",
        "players_per_step": 1,
        "debate_rounds": 0,
        "player_pool": ["data_analyst"],
    },
}
```

**Note:** For multi_csv contexts, `relationship_analyst` is automatically added to the player pool by the orchestrator. No separate multi_csv topologies are needed.

## Tools (`src/tools/`)

### Unified Context Tools (`src/tools/context_tools.py`)

All tools work with the ExecutionContext abstraction:

| Tool | Description |
|------|-------------|
| `get_dataset_overview` | Overview of the entire dataset |
| `list_tables` | List all tables |
| `get_dataset_schema` | Complete schema with relationships |
| `get_resource_info` | Detailed resource information |
| `get_row_count` | Row count for a table |
| `get_column_names` | Column names |
| `get_column_types` | Column data types |
| `get_sample_rows` | Preview rows |
| `get_column_statistics` | Statistics for all columns |
| `get_missing_values` | Missing value counts |
| `get_unique_values` | Unique values in a column |
| `get_relationships` | Get discovered relationships |
| `analyze_potential_relationship` | Analyze a specific relationship |
| `preview_join` | Preview joining two tables |
| `find_common_columns` | Find shared columns across tables |
| `compare_table_schemas` | Compare table structures |

## Metadata Standards (`src/standards.py`)

Predefined output formats:

- `basic`: Simple title, description, schema
- `dublin_core`: Dublin Core metadata standard
- `relational`: Full relational dataset metadata with tables and relationships
- `relational_simple`: Simplified relational format for quick analysis
- `ecological_data`: Specialized format for ecological/scientific datasets

## Usage Examples

### Single File

```python
from src.orchestrator import run_metadata_extraction
from src.standards import METADATA_STANDARDS

result = run_metadata_extraction(
    source="./data/users.csv",
    metadata_standard=METADATA_STANDARDS["basic"]
)
print(result.final_metadata)
```

### Multiple Related Files

```python
result = run_metadata_extraction(
    source={
        "users": "./data/users.csv",
        "orders": "./data/orders.csv",
        "products": "./data/products.csv"
    },
    metadata_standard=METADATA_STANDARDS["relational"]
)

# Access per-resource metadata
for resource, metadata in result.resource_metadata.items():
    print(f"{resource}: {metadata}")

# Access discovered relationships
for rel in result.relationships:
    print(f"{rel['from_resource']}.{rel['from_field']} -> {rel['to_resource']}.{rel['to_field']}")
```

### SQLite Database

```python
result = run_metadata_extraction(
    source="./data/mydb.sqlite",
    metadata_standard=METADATA_STANDARDS["relational"]
)
```

### Directory of Files

```python
result = run_metadata_extraction(
    source="./data/my_dataset/",
    metadata_standard=METADATA_STANDARDS["relational"]
)
```

### Using ExecutionContext Directly

```python
from src.context import create_context
from src.orchestrator import Orchestrator

# Create and inspect ExecutionContext first
ctx = create_context("./data/my_dataset/")
print(f"Resources: {ctx.resources}")
print(f"Relationships: {ctx.get_relationships()}")

# Then run orchestration
orchestrator = Orchestrator(topology_name="default")
result = orchestrator.run(ctx, METADATA_STANDARDS["relational"])
```

## File Structure

```
src/
├── main.py                    # CLI entry point
├── standards.py               # Metadata standards
├── topology.py                # Execution topology configs
├── config.py                  # LLM and system configuration
├── context/                   # Unified context abstraction layer
│   ├── __init__.py            # Exports ExecutionContext, create_context
│   ├── base_context.py        # Abstract ExecutionContext base class
│   ├── csv_context.py         # CSV context implementation
│   ├── context_factory.py     # ContextFactory with auto-detection
│   └── context_classifier.py  # Context classification helpers
├── orchestrator/
│   ├── orchestrator.py        # Main Orchestrator class (unified interface)
│   ├── plan_executor.py       # Executes full plans
│   ├── step_executor.py       # LangGraph for step debates
│   ├── prompts.py             # All prompt templates
│   ├── schemas.py             # Pydantic models
│   └── state.py               # State TypedDicts
├── players/
│   ├── __init__.py            # Exports Player, PLAYER_CONFIGS
│   ├── player.py              # Unified Player class
│   └── configs.py             # Player role configurations
└── tools/
    ├── __init__.py            # Exports all tools
    └── context_tools.py       # Unified ExecutionContext tools
```

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```
GOOGLE_API_KEY=your_api_key_here
```

### Adding New Data Source Types

1. Create a new class extending `ExecutionContext` in `src/context/`
2. Implement required abstract methods
3. Add type detection to `ContextFactory`

Example for Parquet:
```python
class ParquetContext(ExecutionContext):
    @property
    def context_type(self) -> ContextType:
        return ContextType.UNKNOWN
    
    @property
    def resources(self) -> List[str]:
        # Implementation
        
    def _load_resource_info(self, resource: str) -> ResourceInfo:
        # Implementation
        
    def read_resource(self, resource: str, ...) -> pd.DataFrame:
        # Implementation
```

### Adding New Tools

Add to `src/tools/context_tools.py`:

```python
@tool
def my_new_tool(context_key: str, ...) -> Dict[str, Any]:
    """Description of what this tool does."""
    ctx = get_context(context_key)
    # Implementation using ExecutionContext API
    return result
```

Then add to player configs in `src/players/configs.py`.
