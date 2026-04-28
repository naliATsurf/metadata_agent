from src.orchestrator import Orchestrator, run_metadata_extraction
from src.standards import METADATA_STANDARDS
from src.context.context_factory import create_context
from src.orchestrator.plan_executor import PlanExecutor
from src.tools.context_tools import register_context

# 1. Define source
source = {"data": "scratch/sample/biota/biota.csv"}

# 2. Create context
context = create_context(source=source, name="my_dataset")

# 3. Generate plan
orchestrator = Orchestrator(topology_name="fast")
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