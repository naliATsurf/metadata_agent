"""
Main entry point for the Metadata Agent application.

This script uses the Orchestrator with planning + parallel execution + debate
to extract metadata from datasets.

Example Usage:
    # Single CSV file
    python -m src.main --source ./data/my_data.csv --topology default
    
    # Directory of CSVs
    python -m src.main --source ./data/my_dataset/ --topology default
    
    # SQLite database
    python -m src.main --source ./data/mydb.sqlite --metadata-standard relational
"""
import argparse
import logging
import os
import sys
from pprint import pprint

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.standards import METADATA_STANDARDS, load_metadata_standard
from src.topology import EXECUTION_TOPOLOGIES
from src.orchestrator import Orchestrator
from src.context import create_context
# from src.tui import run_tui  # Uncomment if TUI is implemented


def main():
    """
    Main function to run the metadata agent.
    """
    parser = argparse.ArgumentParser(
        description="Run metadata extraction using multi-agent orchestration."
    )
    
    # Required arguments
    parser.add_argument(
        "--source",
        type=str,
        required=False,
        help=(
            "Path to the data source. Can be: "
            "a single CSV file, a directory of CSVs, or a SQLite database. "
            "Required unless --tui is enabled."
        )
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the Textual terminal UI.",
    )
    
    # Configuration arguments
    parser.add_argument(
        "--name",
        type=str,
        default="dataset",
        help="Name for the dataset (used in metadata output)."
    )
    parser.add_argument(
        "--topology",
        type=str,
        default="default",
        choices=list(EXECUTION_TOPOLOGIES.keys()),
        help="The execution topology to use (defines parallelism and debate rounds)."
    )
    parser.add_argument(
        "--metadata-standard",
        type=str,
        default="basic",
        help=(
            "The name of a predefined metadata standard "
            "(e.g., 'basic', 'dublin_core', 'relational') or a file path."
        )
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level."
    )
    
    args = parser.parse_args()

    if args.tui:
        run_tui()
        return
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Validate source exists
    if not args.source:
        logging.error("Missing required argument --source (or use --tui).")
        return

    if not os.path.exists(args.source):
        logging.error(f"Source not found: {args.source}")
        return
    
    # Create ExecutionContext to get info for logging
    try:
        context = create_context(args.source, name=args.name)
    except Exception as e:
        logging.error(f"Failed to create ExecutionContext: {e}")
        return
    
    logging.info("=" * 60)
    logging.info("METADATA AGENT")
    logging.info("=" * 60)
    logging.info(f"Source: {args.source}")
    logging.info(f"Context Name: {context.name}")
    logging.info(f"Context Type: {context.context_type.value}")
    logging.info(f"Resources: {context.resources}")
    logging.info(f"Multi-CSV: {context.is_multi_csv}")
    logging.info(f"Topology: {args.topology}")
    logging.info(f"Metadata Standard: {args.metadata_standard}")
    logging.info("=" * 60)
    
    try:
        metadata_standard_content = load_metadata_standard(args.metadata_standard)
    except ValueError as e:
        logging.error(str(e))
        return
    
    # Initialize and run the orchestrator
    orchestrator = Orchestrator(
        topology_name=args.topology,
        model_name=args.model_name,
        temperature=args.temperature,
        provider=args.provider
    )
    
    result = orchestrator.run(
        source=context,
        metadata_standard=metadata_standard_content
    )
    
    if result is None:
        logging.error("Orchestration failed. No result produced.")
        return
    
    # Print results
    print("\n" + "=" * 60)
    print("EXECUTION COMPLETE")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Steps Completed: {result.steps_completed}/{result.plan_steps_count}")
    
    print("\n--- Step Results ---")
    for step_result in result.step_results:
        print(f"\nStep {step_result.step_index + 1}: {step_result.task}")
        print(f"  Player: {step_result.player_role}")
        print(f"  Success: {step_result.success}")
        print(f"  Debate Rounds: {step_result.debate_rounds_completed}")
        if step_result.consolidated_result:
            print(f"  Result Preview: {step_result.consolidated_result[:200]}...")
    
    print("\n--- Final Workspace Artifacts ---")
    for name, value in result.final_workspace.items():
        preview = str(value)[:100] if value else "None"
        print(f"  {name}: {preview}...")
    
    if result.final_metadata:
        print("\n--- Final Metadata ---")
        pprint(result.final_metadata)


if __name__ == "__main__":
    main()
