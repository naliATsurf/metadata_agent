# Architecture

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

Tools are grouped into *toolsets* by the context capability they need, not by
the file format they came from — so `tabular/` serves CSV and SQLite alike.

| Toolset | Requires | Tools |
|---------|----------|-------|
| `universal` | `ExecutionContext` | `get_context_overview`, `list_resources`, `get_context_schema`, `get_resource_info`, `get_item_count`, `get_sample_items` |
| `universal.relationships` | `ExecutionContext` (multi-resource) | `get_relationships` |
| `tabular.profiling` | `TabularContext` | `get_field_names`, `get_field_types`, `get_field_statistics`, `get_missing_values`, `get_unique_values` |
| `tabular.temporal` | `TabularContext` | `detect_temporal_columns`, `analyze_temporal_column`, `get_temporal_extent` |
| `tabular.spatial` | `TabularContext` | `detect_spatial_columns`, `analyze_spatial_column`, `get_spatial_extent`, `get_spatial_extent_from_tuple_column` |

`tools_for(context)` offers a tool iff the context is an instance of the tool's
declared `requires` class. There is no context-type compatibility table.

### How tools are invoked

Each tool's argument schema decides how it can be called, so nothing needs to be
declared twice:

- **auto-fireable** — `context_key` and `resource` are its only required
  arguments, so the player fires it deterministically in the *survey* phase.
- **model-invoked** — it needs an argument only judgment can supply (a `column`,
  a `field`), so it is bound to the model in the *investigation* phase, seeded
  with the survey output.

Players request toolsets, never individual tools; see `PLAYER_CONFIGS`.

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
    ├── __init__.py            # Registers all toolsets; exports the registry API
    ├── base.py                # @context_tool, capability gating, tools_for()
    ├── universal.py           # Tools needing only ExecutionContext
    └── tabular/               # Tools needing TabularContext (CSV + SQLite)
        ├── detection.py       # Pure column heuristics, no context needed
        ├── profiling.py
        ├── temporal.py
        └── spatial.py
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

Add the tool to the module for its capability — `src/tools/universal.py` if it
needs nothing beyond the base contract, `src/tools/tabular/` if it needs
rows and columns:

```python
@context_tool(toolset="tabular.profiling", requires=TabularContext)
def my_new_tool(ctx: TabularContext, resource: str = "") -> Dict[str, Any]:
    """Description the planner and the model will both read."""
    return {...}
```

The decorator resolves and type-checks the context, so the body receives a live
`ctx` and may raise freely. Declare `requires` honestly: it is what makes the
tool appear for the right contexts and refuse the wrong ones.

Nothing else needs updating. Whether the tool is fired automatically or offered
to the model is derived from its signature, and any player whose `toolsets`
match its `toolset` picks it up. To reach a role that does not yet request that
toolset, add the toolset name to `PLAYER_CONFIGS`.

Use `available_when=` only for constraints capability cannot express — for
instance `lambda ctx: ctx.is_multi_resource`.

### Adding a New Modality

Subclass `ExecutionContext` (or a capability mixin like `TabularContext`),
implement `preview()` and the resource-info contract, register the extension in
`src/context/registry.py`, and add a toolset package if the modality has tools
of its own. Every `universal` tool works on it immediately; no gating table
exists to update.
