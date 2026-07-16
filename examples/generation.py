"""Generate spatial ecological metadata for a dataset.

This example loads a dataset path from the environment, builds a context around
that source, asks the orchestrator to create an extraction plan for the
``spatial_ecological`` metadata standard, executes the plan, and writes the
resulting metadata JSON to disk.

Run from the repository root as a module (running the file directly breaks
the ``examples`` package import)::

    python -m examples.generation
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

logger.info("**************************** Start of Workflow ****************************")

# 1. Define the input source. DATA_FILE should point at the dataset that will be
# inspected when the context is created.
log_step_section(1, "Define source")
source = {"data": example_config.DATA_FILE}
topology_name = agent_config.DEFAULT_TOPOLOGY

logger.info("%s", agent_config.get_config_summary())
logger.info("%s", example_config.config_summary())

step_start = log_step_timing("Define source", step_start)

# 2. Create a context object. The context wraps the source data and gives the
# planner/executor a stable dataset name to reference.
log_step_section(2, "Create context")
context = create_context(source=source, name="my_dataset")
step_start = log_step_timing("Create context", step_start)
logging.info(f"Context: {context.name}")
logging.info(f"Context type: {context.context_type.value}")
logging.info(f"Resources: {context.resources}")
logging.info("=" * 60)


# 3. Ask the orchestrator to generate a metadata extraction plan for the target
# metadata standard. The selected topology controls which planning strategy is
# used, while provider/model/temperature control the LLM call.
log_step_section(3, "Generate plan")
orchestrator = Orchestrator(
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

# 5. Collect and persist the final metadata output produced by the plan, plus its
# per-field provenance sidecar (which tool's output supports each value).
log_step_section(5, "Write metadata and provenance")

# A rejected or failed plan produces no metadata_output; surface that instead of
# crashing on a missing key.
metadata_output = result.final_workspace.get('metadata_output')
if metadata_output is None:
    logger.error(
        "No metadata produced — the plan was rejected or execution failed: %s",
        result.error,
    )
    logger.info("**************************** End of Workflow (aborted) ****************************")
    raise SystemExit(1)

provenance = result.final_provenance or {}
evidence = result.final_evidence or []
output_dir = Path(example_config.OUTPUT_DIR)
output_dir.mkdir(parents=True, exist_ok=True)
with (output_dir / f"metadata_{context.name}.json").open("w", encoding="utf-8") as f:
    json.dump(metadata_output, f, ensure_ascii=False, indent=2, default=str)
with (output_dir / f"provenance_{context.name}.json").open("w", encoding="utf-8") as f:
    json.dump(provenance, f, ensure_ascii=False, indent=2, default=str)
with (output_dir / f"evidence_{context.name}.json").open("w", encoding="utf-8") as f:
    json.dump(evidence, f, ensure_ascii=False, indent=2, default=str)

metadata_json = json.dumps(metadata_output, ensure_ascii=False, indent=2, default=str)
provenance_json = json.dumps(provenance, ensure_ascii=False, indent=2, default=str)
# The evidence ledger can be large; log a one-line-per-fact summary and leave the
# full payload for evidence_<name>.json. Seeing what the tools produced is what
# makes an 'unverifiable' field interpretable.
evidence_summary = "\n".join(
    f"  {e['id']}  {e['citation']}" for e in evidence
) or "  (no tool evidence captured this run)"
if example_config.LOG_MODE == "quiet":
    print(metadata_json)
    print("\nProvenance:")
    print(provenance_json)
    print(f"\nEvidence ({len(evidence)} facts):")
    print(evidence_summary)
else:
    logger.info("Extracted Metadata:")
    logger.info("%s", metadata_json)
    logger.info("Provenance:\n%s", provenance_json)
    logger.info("Evidence (%d facts captured):\n%s", len(evidence), evidence_summary)
step_start = log_step_timing("Write metadata, provenance and evidence", step_start)
logger.info("[timer] Whole process: %.3fs", step_start - process_start)
logger.info("**************************** End of Workflow ****************************")
