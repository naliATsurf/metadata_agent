from src.orchestrator import Orchestrator, run_metadata_extraction
from src.standards import METADATA_STANDARDS
from src.context.context_factory import create_context
from src.orchestrator.plan_executor import PlanExecutor
from src.tools.context_tools import register_context
from src.config import LLM_PROVIDER, PLANNING_TEMPERATURE, get_model_name
from pprint import pprint
import json
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# 1. Define source
source = {"data": os.getenv("DATA_FILE")}

# 2. Create context
context = create_context(source=source, name="my_dataset")

print("------------------- Starting Plan Generation and Execution -------------------")
print(f"Data source: {source['data']}")
print(f"Model name: {get_model_name()}")
print(f"LLM Provider: {LLM_PROVIDER}")
print(f"Planning temperature: {PLANNING_TEMPERATURE}")

# 3. Generate plan
orchestrator = Orchestrator(
    topology_name="fast",
    model_name=get_model_name(),
    temperature=PLANNING_TEMPERATURE,
    provider=LLM_PROVIDER,
)
plan = orchestrator.generate_plan(
    context=context,
    metadata_standard=METADATA_STANDARDS["spatial_ecological"]
)
print("Generated Plan:")
pprint(plan)


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
metadata_output = result.final_workspace['metadata_output']
output_dir = Path("scratch/output")
output_dir.mkdir(parents=True, exist_ok=True)
with (output_dir / f"metadata_{context.name}.json").open("w", encoding="utf-8") as f:
    json.dump(metadata_output, f, ensure_ascii=False, indent=2, default=str)

print("Extracted Metadata:")
print(metadata_output)
print("------------------- End of Execution -------------------")
