# Metadata Agent Tutorial

This tutorial walks you through using the Multi-Agent System (MAS) for automatic metadata extraction from datasets. We'll use a practical example with ecological data to demonstrate the full workflow.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [LLM Provider Configuration](#llm-provider-configuration)
3. [Getting Started](#getting-started)
4. [Core Workflow](#core-workflow)
5. [Step-by-Step Example](#step-by-step-example)
6. [Understanding the Output](#understanding-the-output)
7. [Configuration Options](#configuration-options)
8. [Tips and Best Practices](#tips-and-best-practices)

---

## Prerequisites

Before starting, ensure you have:

- Python 3.9+ installed
- Required dependencies installed (`pip install -r requirements.txt`)
- LLM provider configured (see [LLM Provider Configuration](#llm-provider-configuration))

---

## LLM Provider Configuration

The Metadata Agent supports multiple LLM providers. Configure your preferred provider in a `.env` file in the project root.

### Supported Providers

| Provider | Description | Required Environment Variables |
|----------|-------------|-------------------------------|
| `google` | Google Gemini models (default) | `GOOGLE_API_KEY` |
| `surf` | SURF Research Cloud (Willma) | `SURF_API_KEY`, `SURF_API_BASE` |
| `openai` | OpenAI models | `OPENAI_API_KEY` |

### Option 1: Google Gemini (Default)

```bash
# .env file
LLM_PROVIDER=google
GOOGLE_API_KEY=your_google_api_key_here

# Optional: specify model (default: gemini-2.5-flash)
LLM_MODEL=gemini-2.5-flash
```

### Option 2: SURF Research Cloud (Willma)

SURF provides access to open-source LLMs through the [Willma platform](https://willma.surf.nl). This is ideal for Dutch research institutions.

**Step 1: Get your API key**

1. Log in to [willma.surf.nl](https://willma.surf.nl)
2. Navigate to your account settings
3. Generate or copy your API key

**Step 2: Configure environment**

```bash
# .env file
LLM_PROVIDER=surf
SURF_API_KEY=your_surf_api_key_here
SURF_API_BASE=https://willma.surf.nl/api/v0

# Optional: specify model
LLM_MODEL=Qwen 2.5 Coder 32B Instruct AWQ
```

**Step 3: Test your connection**

```python
import requests
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("SURF_API_KEY")
headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
willma_base_url = "https://willma.surf.nl/api/v0"

# List available models
models = requests.get(f"{willma_base_url}/sequences", headers=headers).json()
print("Available models:")
for m in models:
    if m['sequence_type'] == 'text':
        print(f"  - {m['name']}: {m['description']}")
```

**Step 4: Test a completion**

```python
import json

response = requests.post(
    f"{willma_base_url}/chat/completions",
    data=json.dumps({
        "model": "Qwen 2.5 Coder 32B Instruct AWQ",
        "messages": [{"role": "user", "content": "Hello! What can you do?"}],
    }),
    headers=headers
).json()

print(response["choices"][0]["message"]["content"])
```

### Option 3: OpenAI

```bash
# .env file
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key_here

# Optional: specify model (default: gpt-4o-mini)
LLM_MODEL=gpt-4o-mini
```

### Additional Configuration Options

```bash
# .env file

# LLM temperatures
LLM_TEMPERATURE_PLANNING=0.0    # For deterministic planning
LLM_TEMPERATURE_PLAYER=0.3     # For creative player responses

# Defaults
DEFAULT_TOPOLOGY=default       # Execution topology
DEFAULT_METADATA_STANDARD=basic
```

### Verifying Your Configuration

```python
from src.config import get_config_summary

print(get_config_summary())
```

Example output:
```
Configuration Summary:
----------------------
LLM Provider: surf (Custom OpenAI-compatible endpoint)
LLM Model: Qwen 2.5 Coder 32B Instruct AWQ
Planning Temperature: 0.0
Player Temperature: 0.3
Default Topology: default
Default Metadata Standard: basic
API Key (SURF_API_KEY): Set
```

---

## Getting Started

### Basic Imports

First, import the necessary modules:

```python
import sys
import os

# Add project root to path (if running from notebooks/)
sys.path.insert(0, '..')

from src.orchestrator import Orchestrator
from src.standards import METADATA_STANDARDS
from src.context.context_factory import create_context

# Set base path for data files
BASE = os.path.abspath(os.path.join('..'))
```

### Setting Up Logging (Optional)

For better visibility into the system's operations, configure logging:

```python
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Remove existing handlers to avoid duplicates
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Add a StreamHandler for visible output
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
logger.addHandler(handler)
```

---

## Core Workflow

The metadata extraction process follows four main steps:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  1. Create      │ ──► │  2. Generate    │ ──► │  3. Execute     │ ──► │  4. Extract     │
│     Context     │     │     Plan        │     │     Plan        │     │     Metadata    │
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

1. **Create Context**: Load your data source(s) into a unified context
2. **Generate Plan**: The orchestrator creates an execution plan based on your data and metadata standard
3. **Execute Plan**: Multiple AI players execute the plan with debate and synthesis
4. **Extract Metadata**: Get the final structured metadata output

---

## Step-by-Step Example

Let's walk through a complete example using a multi-file ecological dataset.

### Step 1: Define Your Data Sources

You can work with single files or multiple related files. Here's an example with multiple CSV files:

```python
from src.orchestrator import run_metadata_extraction

# Define multiple related data sources
multi_source = {
    'biota': os.path.join(BASE, 'data/biota_dataset/biota.csv'),
    'samples': os.path.join(BASE, 'data/biota_dataset/samples.csv'),
    'species': os.path.join(BASE, 'data/biota_dataset/species.csv'),
}
```

**Supported input formats:**
- Single file: `"path/to/file.csv"`
- Multiple files (dict): `{"name1": "path1.csv", "name2": "path2.csv"}`
- Multiple files (list): `["file1.csv", "file2.csv"]`
- Directory: `"path/to/directory/"`
- SQLite database: `"path/to/database.sqlite"`

### Step 2: Create a Context

The context wraps your data sources and provides a unified interface:

```python
data_context = create_context(
    source=multi_source,
    name='biota_multi'
)
```

The context automatically:
- Detects the data format
- Discovers schemas and data types
- Identifies relationships between resources

### Step 3: Choose a Metadata Standard

Select a metadata standard that matches your use case:

```python
metadata_standard = METADATA_STANDARDS['spatial_ecological']
```

**Available standards:**
| Standard | Description |
|----------|-------------|
| `spatial_ecological` | For spatial/ecological datasets with geographic and temporal coverage |

> 💡 You can also define custom metadata standards—see [Adding Custom Standards](#adding-custom-standards).

### Step 4: Initialize the Orchestrator

Create an orchestrator with your preferred execution topology:

```python
from src.config import LLM_PROVIDER, PLANNING_TEMPERATURE, get_default_model

orchestrator = Orchestrator(
    topology_name="fast",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)
```

**Available topologies:**
| Topology | Players | Debate Rounds | Use Case |
|----------|---------|---------------|----------|
| `single` | 1 | 0 | Quick testing, minimal cost |
| `fast` | 2 | 1 | Rapid extraction with basic quality |
| `default` | 3 | 2 | Balanced speed and quality |
| `thorough` | 4 | 3 | Maximum quality, higher cost |

### Step 5: Generate a Plan

Generate an execution plan based on your context and metadata standard:

```python
plan = orchestrator.generate_plan(
    context=data_context,
    metadata_standard=metadata_standard
)
```

The system will output log messages showing:
- Context information (name, type, resources)
- Discovered relationships between resources
- Available players in the topology

### Step 6: Validate and Inspect the Plan

Before execution, validate the plan's dataflow:

```python
from src.orchestrator.utils import validate_plan_dataflow

if plan:
    plan_dicts = plan.to_dict_list()
    is_valid, message = validate_plan_dataflow(plan_dicts)
    
    if is_valid:
        print(f"✅ {message}")
    else:
        print(f"❌ {message}")
```

View the plan structure:

```python
plan.pretty_print()
```

Example output:

```
Plan Steps:
Step 0: get_context_overview
  Rationale: To get an overview of the entire context including all resources.
  Required Artifacts: {}
  Produced Artifacts: ['context_overview']
  
Step 1: get_context_schema
  Rationale: To understand the schema of the context...
  Required Artifacts: {}
  Produced Artifacts: ['context_schema']
  
Step 2: get_relationships
  Rationale: To discover and validate relationships between the resources.
  Required Artifacts: {}
  Produced Artifacts: ['discovered_relationships']
  
Step 3: metadata_generator
  Rationale: To generate concrete metadata values...
  Required Artifacts: {'metadata_standard': 'metadata_standard', ...}
  Produced Artifacts: ['metadata_output']
```

### Step 7: Execute the Plan

Now execute the plan to extract metadata:

```python
from src.orchestrator.plan_executor import PlanExecutor
from src.tools.context_tools import register_context

# Register context for tool access
context_key = "ctx_biota_multi"
register_context(context_key, data_context)

# Create executor and run
executor = PlanExecutor(topology_name="fast")
result = executor.execute(
    plan=plan,
    context=data_context,
    context_key=context_key,
    metadata_standard=metadata_standard,
    metadata_standard_name="spatial_ecological"
)
```

During execution, you'll see:
- Each step's progress
- Player assignments and execution
- Debate rounds (critique → revise cycles)
- Synthesis of results

### Step 8: Access the Results

Extract the final metadata from the result:

```python
from pprint import pprint

pprint(result.final_workspace['metadata_output'])
```

Example output:

```python
{
    'title': 'Biota Multi-Resource Dataset',
    'description': 'This dataset contains biological data collected from multiple '
                   'samples across various tidal basins and flats in the '
                   'Netherlands. It includes abundance and biomass data for '
                   'different species, along with metadata about the sampling '
                   'locations, dates, and methods.',
    'subject': 'Biological data, tidal basins, species abundance, biomass',
    'spatial_coverage': 'Various tidal basins and flats in the Netherlands, '
                        'including Marsdiep, Eierlandse Gat, Vlie, Balgzand...',
    'spatial_resolution': 'Coordinates provided with precision up to six decimal '
                          'places for longitude (x) and latitude (y).',
    'temporal_coverage': '2008-2024',
    'temporal_resolution': 'Sampling conducted irregularly, with the most common '
                           'date being 2012-06-19.',
    'methods': 'Sampling conducted primarily using boats with various methods '
               'such as grid and random sampling.',
    'format': 'CSV'
}
```

---

## Understanding the Output

### The Result Object

The `result` object contains:

| Attribute | Description |
|-----------|-------------|
| `final_workspace` | Dictionary containing all produced artifacts |
| `final_workspace['metadata_output']` | The extracted metadata in your chosen standard |
| `final_workspace['context_overview']` | Overview of the dataset |
| `final_workspace['context_schema']` | Schema information |
| `final_workspace['discovered_relationships']` | Relationships between resources |

### Metadata Fields (spatial_ecological standard)

| Field | Description |
|-------|-------------|
| `title` | Dataset title |
| `description` | Comprehensive description of the dataset |
| `subject` | Subject/topic keywords |
| `spatial_coverage` | Geographic coverage area |
| `spatial_resolution` | Precision of spatial data |
| `temporal_coverage` | Time period covered |
| `temporal_resolution` | Frequency of temporal data |
| `methods` | Data collection methods |
| `format` | Data format (CSV, SQLite, etc.) |

---

## Configuration Options

### Execution Topologies

Choose based on your needs:

```python
from src.config import LLM_PROVIDER, PLANNING_TEMPERATURE, get_default_model

# Quick testing - single player, no debate
orchestrator = Orchestrator(
    topology_name="single",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)

# Fast extraction - 2 players, 1 debate round
orchestrator = Orchestrator(
    topology_name="fast",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)

# Balanced - 3 players, 2 debate rounds (recommended)
orchestrator = Orchestrator(
    topology_name="default",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)

# Thorough - 4 players, 3 debate rounds
orchestrator = Orchestrator(
    topology_name="thorough",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)
```

### Player Types

The system uses specialized AI players:

| Player | Role |
|--------|------|
| `data_analyst` | Analyzes data structure and content |
| `schema_expert` | Examines schemas and data types |
| `metadata_specialist` | Focuses on metadata best practices |
| `relationship_analyst` | Discovers resource relationships (auto-added for `multi_csv` contexts) |
| `critic` | Reviews and critiques other players' work |

---

## Tips and Best Practices

### 1. Start with the `fast` Topology

For initial exploration, use the `fast` topology to get quick results:

```python
from src.config import LLM_PROVIDER, PLANNING_TEMPERATURE, get_default_model

orchestrator = Orchestrator(
    topology_name="fast",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)
```

Switch to `default` or `thorough` for production use.

### 2. Use Meaningful Context Names

Give your context a descriptive name:

```python
data_context = create_context(
    source=multi_source,
    name='netherlands_biota_survey_2024'  # Descriptive name
)
```

### 3. Validate Before Executing

Always validate the plan before execution:

```python
is_valid, message = validate_plan_dataflow(plan.to_dict_list())
if not is_valid:
    print(f"Plan invalid: {message}")
    # Handle the error
```

### 4. Monitor Execution with Logging

Enable INFO-level logging to monitor progress:

```python
logging.getLogger().setLevel(logging.INFO)
```

### 5. Handle Large Datasets

For large datasets, the system samples data intelligently. You don't need to pre-process your data.

### 6. Use the Quick API

For simple use cases, use the convenience function:

```python
from src.orchestrator import run_metadata_extraction

result = run_metadata_extraction(
    source="./data/my_dataset.csv",
    metadata_standard=METADATA_STANDARDS["spatial_ecological"]
)
```

---

## Quick Reference

### Minimal Working Example

```python
from src.orchestrator import Orchestrator, run_metadata_extraction
from src.standards import METADATA_STANDARDS
from src.context.context_factory import create_context
from src.orchestrator.plan_executor import PlanExecutor
from src.tools.context_tools import register_context
from src.config import LLM_PROVIDER, PLANNING_TEMPERATURE, get_default_model

# 1. Define source
source = {"data": "path/to/data.csv"}

# 2. Create context
context = create_context(source=source, name="my_dataset")

# 3. Generate plan
orchestrator = Orchestrator(
    topology_name="fast",
    model_name=get_default_model(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)
plan = orchestrator.generate_plan(
    context=context,
    metadata_standard=METADATA_STANDARDS["spatial_ecological"]
)

# 4. Execute
context_key = "ctx_my_dataset"
register_context(context_key, context)

executor = PlanExecutor(topology_name="fast")
result = executor.execute(
    plan=plan,
    context=context,
    context_key=context_key,
    metadata_standard=METADATA_STANDARDS["spatial_ecological"],
    metadata_standard_name="spatial_ecological"
)

# 5. Get metadata
print(result.final_workspace['metadata_output'])
```

---

## Next Steps

- See [Architecture Documentation](architecture.md) for system internals
- Explore the example notebooks in `notebooks/`
- Check `src/standards.py` for adding custom metadata standards
