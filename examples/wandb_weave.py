"""Generate spatial ecological metadata for a dataset.

This example loads a dataset path from the environment, builds a context around
that source, asks the orchestrator to create an extraction plan for the
``spatial_ecological`` metadata standard, executes the plan, and writes the
resulting metadata JSON to disk.
"""

from src.orchestrator import Orchestrator
from src.standards import METADATA_STANDARDS
from src.context.context_factory import create_context
from src import config as agent_config
from examples import config as example_config
import json
import logging
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

import wandb
import weave
weave.init("metadata-agent-wandb-weave")

class TrackedOrchestrator(Orchestrator):
    @weave.op(name="Orchestrator.generate_plan")
    def generate_plan(self, *args, **kwargs):
        return super().generate_plan(*args, **kwargs)

    @weave.op(name="Orchestrator.execute_plan")
    def execute_plan(self, *args, **kwargs):
        return super().execute_plan(*args, **kwargs)

    @weave.op(name="Orchestrator.run")
    def run(self, *args, **kwargs):
        return super().run(*args, **kwargs)

# Three logging modes are available:
# "full" prints all logs including those used in other modules;
# "top" prints only the message content inside this script;
# "quiet" suppresses logs and prints only the final metadata output.
if example_config.LOG_MODE == "quiet":
    logging.disable(logging.CRITICAL)
    logger = logging.getLogger(__name__)
elif example_config.LOG_MODE == "full":
    logging.basicConfig(
        level=getattr(logging, example_config.LOG_LEVEL),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
elif example_config.LOG_MODE == "top":
    logger = logging.getLogger(__name__)
    logger.setLevel(getattr(logging, example_config.LOG_LEVEL))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
else:
    raise ValueError(f"Invalid LOG_MODE: {example_config.LOG_MODE}. Must be 'full', 'top', or 'quiet'.")


process_start = perf_counter()
step_start = process_start


def log_step_section(step_number: int, step_name: str) -> None:
    """Print a visible section header for each major example step."""
    logger.info("")
    logger.info("------------------- PART %d: %s -------------------", step_number, step_name)


def log_step_timing(step_name: str, start: float) -> float:
    """Log elapsed time for a step and return the next timer start value."""
    now = perf_counter()
    logger.info("[timer] %s: %.3fs", step_name, now - start)
    return now


def artifact_reference_uri(path_or_uri: str) -> str:
    """Return a W&B artifact reference URI for local paths and remote URIs."""
    parsed = urlparse(path_or_uri)
    if parsed.scheme:
        return path_or_uri
    return Path(path_or_uri).expanduser().resolve().as_uri()

logger.info("**************************** Start of Workflow ****************************")

# 1. Define the input source. DATA_FILE should point at the dataset that will be
# inspected when the context is created.
log_step_section(1, "Define source")
source = {"data": example_config.DATA_FILE}
topology_name = agent_config.DEFAULT_TOPOLOGY

with wandb.init(
    project="metadata-agent-wandb-weave",
    name="spatial-ecological-my-dataset",
) as run:
    dataset_artifact = wandb.Artifact(name="input_dataset", type="dataset")
    # dataset_artifact.add_file(example_config.DATA_FILE)
    dataset_artifact.add_reference(artifact_reference_uri(example_config.DATA_FILE))

    run.log_artifact(dataset_artifact)

logger.info("%s", agent_config.get_config_summary())
logger.info("%s", example_config.config_summary())

step_start = log_step_timing("Define source", step_start)

# 2. Create a context object. The context wraps the source data and gives the
# planner/executor a stable dataset name to reference.
log_step_section(2, "Create context")
context = create_context(source=source, name="my_dataset")
step_start = log_step_timing("Create context", step_start)


# 3. Ask the orchestrator to generate a metadata extraction plan for the target
# metadata standard. The selected topology controls which planning strategy is
# used, while provider/model/temperature control the LLM call.
log_step_section(3, "Generate plan")

orchestrator = TrackedOrchestrator(
    topology_name=topology_name,
    model_name=agent_config.get_model_name(),
    temperature=agent_config.PLANNING_TEMPERATURE,
    provider=agent_config.LLM_PROVIDER,
)
plan = orchestrator.generate_plan(
    context=context,
    metadata_standard=METADATA_STANDARDS["spatial_ecological"]
)
step_start = log_step_timing("Generate plan", step_start)

# Pydantic v2 models expose model_dump; keep the fallback so this example also
# works if generate_plan returns a plain serializable object.
plan_payload = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
logger.info("Generated Plan:\n%s", json.dumps(plan_payload, indent=2, default=str))


# 4. Execute the generated plan. The orchestrator will execute each step in the plan, making calls to tools
log_step_section(4, "Execute plan")

result = orchestrator.execute_plan(
    plan=plan,
    context=context,
    metadata_standard=METADATA_STANDARDS["spatial_ecological"],
    metadata_standard_name="spatial_ecological"
)
step_start = log_step_timing("Execute plan", step_start)

# 5. Collect and persist the final metadata output produced by the plan.
log_step_section(5, "Write metadata")
metadata_output = result.final_workspace['metadata_output']
output_dir = Path(example_config.OUTPUT_DIR)
output_dir.mkdir(parents=True, exist_ok=True)
with (output_dir / f"metadata_{context.name}.json").open("w", encoding="utf-8") as f:
    json.dump(metadata_output, f, ensure_ascii=False, indent=2, default=str)

logger.info("Extracted Metadata:")
metadata_json = json.dumps(metadata_output, ensure_ascii=False, indent=2, default=str)
if example_config.LOG_MODE == "quiet":
    print(metadata_json)
else:
    logger.info("%s", metadata_json)
step_start = log_step_timing("Write metadata", step_start)
logger.info("[timer] Whole process: %.3fs", step_start - process_start)
logger.info("**************************** End of Workflow ****************************")
