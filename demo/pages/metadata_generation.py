"""Streamlit page for the metadata generation demo.

The page owns only UI concerns: collecting user inputs, showing a data preview,
triggering the workflow, caching results in Streamlit session state, and
rendering the workflow output.
"""

import json
import multiprocessing
from pathlib import Path
from queue import Empty
from time import monotonic, perf_counter
from typing import Any, get_args
from xml.etree import ElementTree

from pydantic import BaseModel
import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile
import yaml

from demo.workflows.metadata_generation import (
    SUPPORTED_FILE_TYPES,
    available_metadata_standards,
    execution_details,
    extract_metadata,
    generate_metadata,
    load_preview,
    uploaded_file_key,
)
from src.standards import get_schema_for_standard


GENERATION_STEPS = (
    ("Context", "creating_context", "context_created"),
    ("Agent setup", "initializing_orchestrator", "generating_plan"),
    ("Plan", "generating_plan", "plan_generated"),
    ("Execution", "executing_plan", "execution_complete"),
)

CURRENT_STAGE_LABELS = {
    "creating_context": "Creating execution context",
    "context_created": "Execution context created",
    "initializing_orchestrator": "Initializing metadata agent",
    "generating_plan": "Generating execution plan",
    "plan_generated": "Execution plan generated",
    "executing_plan": "Executing metadata plan",
    "execution_complete": "Metadata generation complete",
}

EXPORT_FORMATS = {
    "JSON": ("json", "application/json"),
    "YAML": ("yaml", "application/yaml"),
    "XML": ("xml", "application/xml"),
}
RESULT_TAB_KEY = "metadata_result_tab"


def metadata_as_xml(metadata: Any) -> str:
    """Serialize nested metadata as an XML document."""
    root = ElementTree.Element("metadata")

    def append_value(parent: ElementTree.Element, value: Any) -> None:
        if isinstance(value, dict):
            for key, child_value in value.items():
                child = ElementTree.SubElement(parent, "field", name=str(key))
                append_value(child, child_value)
        elif isinstance(value, list):
            for child_value in value:
                child = ElementTree.SubElement(parent, "item")
                append_value(child, child_value)
        elif value is not None:
            parent.text = str(value).lower() if isinstance(value, bool) else str(value)

    append_value(root, metadata)
    ElementTree.indent(root)
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)


def serialize_metadata(metadata: Any, export_format: str) -> str:
    """Serialize metadata in the selected export format."""
    if export_format == "JSON":
        return json.dumps(metadata, indent=2, ensure_ascii=False, default=str)
    if export_format == "XML":
        return metadata_as_xml(metadata)
    return yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)


def build_generation_timing(
    event_times: dict[str, float], total_seconds: float
) -> dict[str, Any]:
    """Build displayable workflow and step durations from progress timestamps."""
    steps = []
    for label, start_stage, end_stage in GENERATION_STEPS:
        started_at = event_times.get(start_stage)
        completed_at = event_times.get(end_stage)
        if started_at is not None and completed_at is not None:
            steps.append(
                {"step": label, "seconds": max(0.0, completed_at - started_at)}
            )
    return {"total_seconds": total_seconds, "steps": steps}


def format_field_type(annotation: Any) -> str:
    """Return a compact, readable field type name."""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "").replace("NoneType", "None")


def nested_model_for(annotation: Any) -> type[BaseModel] | None:
    """Return the Pydantic model nested inside a field annotation, if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for argument in get_args(annotation):
        nested_model = nested_model_for(argument)
        if nested_model is not None:
            return nested_model
    return None


def flatten_schema_fields(
    schema: type[BaseModel],
    prefix: str = "",
    parent_required: bool = True,
    ancestors: frozenset[type[BaseModel]] = frozenset(),
) -> list[dict[str, str]]:
    """Flatten nested Pydantic fields into rows with dotted field paths."""
    if schema in ancestors:
        return []

    rows = []
    nested_ancestors = ancestors | {schema}
    for name, field in schema.model_fields.items():
        field_path = f"{prefix}.{name}" if prefix else name
        is_required = parent_required and field.is_required()
        rows.append(
            {
                "Field": field_path,
                "Type": format_field_type(field.annotation),
                "Requirement": "Required" if is_required else "Optional",
                "Description": field.description or "",
            }
        )

        nested_schema = nested_model_for(field.annotation)
        if nested_schema is not None:
            rows.extend(
                flatten_schema_fields(
                    nested_schema,
                    prefix=field_path,
                    parent_required=is_required,
                    ancestors=nested_ancestors,
                )
            )
    return rows


def render_upload_control() -> UploadedFile | None:
    """Render the dataset upload control."""
    return st.file_uploader(
        "🗂️ Upload dataset(s)",
        type=SUPPORTED_FILE_TYPES,
        help="CSV and TSV files are supported by the demo context.",
    )


def render_standard_control() -> str:
    """Render the metadata standard selection control."""
    standard_name = st.selectbox(
        "🧩 Choose a metadata standard",
        options=available_metadata_standards(),
        index=0,
    )
    schema = get_schema_for_standard(standard_name)
    fields = schema.model_fields.values() if schema is not None else ()
    field_count = len(schema.model_fields) if schema is not None else 0
    required_count = sum(field.is_required() for field in fields)
    optional_count = field_count - required_count
    st.caption(
        f"{field_count} fields · {required_count} required · "
        f"{optional_count} optional"
    )
    return standard_name


def render_standard_preview(standard_name: str) -> None:
    """Render flattened field details for a metadata standard."""
    with st.expander("Metadata standard preview", expanded=False):
        schema = get_schema_for_standard(standard_name)
        if schema is not None:
            st.dataframe(
                flatten_schema_fields(schema),
                width="stretch",
                hide_index=True,
            )


def render_preview(uploaded_file: UploadedFile, file_bytes: bytes) -> None:
    """Render a preview table for the uploaded dataset.

    Args:
        uploaded_file: Streamlit uploaded file object.
        file_bytes: Raw uploaded file bytes.
    """
    with st.expander("Data preview", expanded=False):
        try:
            st.dataframe(
                load_preview(uploaded_file.name, file_bytes, rows=8),
                width='stretch',
            )
            st.caption(f"{uploaded_file.name} - {len(file_bytes):,} bytes")
        except Exception as exc:
            st.warning(f"Preview unavailable: {exc}")


def run_generation(
    file_name: str,
    file_bytes: bytes,
    standard_name: str,
    messages: multiprocessing.Queue,
) -> None:
    """Run metadata generation in a child process and publish its output.

    Args:
        file_name: Name of the uploaded file.
        file_bytes: Raw uploaded file bytes.
        standard_name: Selected metadata standard name.
        messages: Queue used to publish progress and the final result.
    """
    started_at = perf_counter()
    event_times: dict[str, float] = {}

    def show_progress(stage: str, payload: Any | None) -> None:
        elapsed = perf_counter() - started_at
        event_times[stage] = elapsed
        messages.put(("progress", stage, payload, elapsed))

    try:
        result = generate_metadata(
            file_name,
            file_bytes,
            standard_name,
            progress_callback=show_progress,
        )
        total_seconds = perf_counter() - started_at
        result["generation_timing"] = build_generation_timing(
            event_times, total_seconds
        )
        messages.put(("result", result))
    except Exception as exc:
        messages.put(("error", repr(exc)))


def stop_generation(job: dict[str, Any]) -> None:
    """Terminate an active metadata generation process."""
    process = job["process"]
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)


def render_result(result: dict[str, Any]) -> None:
    """Render generated metadata and compact execution details.

    Args:
        result: JSON-friendly metadata generation result.
    """
    metadata_tab, plan_tab, details_tab = st.tabs(
        ["Metadata", "Plan", "Execution details"],
        key=RESULT_TAB_KEY,
        on_change="rerun",
    )
    metadata = extract_metadata(result)
    plan = result.get("generated_plan") or []

    with metadata_tab:
        if metadata:
            display_format = st.session_state.get("metadata_export_format", "JSON")
            if display_format == "JSON":
                st.code(
                    serialize_metadata(metadata, "JSON"),
                    language="json",
                    line_numbers=True,
                    wrap_lines=True,
                )
            else:
                extension, _ = EXPORT_FORMATS[display_format]
                serialized_metadata = serialize_metadata(metadata, display_format)
                st.code(
                    serialized_metadata,
                    language=extension,
                    line_numbers=True,
                    wrap_lines=True,
                )
        else:
            st.warning("No final metadata artifact was found in the result.")

    with plan_tab:
        if plan:
            st.json(plan)
        else:
            st.info("No generated plan is available.")

    step_results = result.get("step_results") or []
    with details_tab:
        timing = result.get("generation_timing") or {}
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Plan steps", result.get("plan_steps_count", 0))
        col2.metric("Completed", result.get("steps_completed", 0))
        col3.metric("Success", "Yes" if result.get("success") else "No")
        col4.metric("Workflow time", f"{timing.get('total_seconds', 0):.1f}s")

        if timing.get("steps"):
            st.dataframe(
                [
                    {"step": step["step"], "time": f"{step['seconds']:.1f}s"}
                    for step in timing["steps"]
                ],
                width="stretch",
                hide_index=True,
            )

        if result.get("error"):
            st.error(result["error"])

        if step_results:
            st.subheader("Execution steps")
            st.dataframe(
                [
                    {
                        "step": step.get("step_index", 0) + 1,
                        "task": step.get("task"),
                        "player": step.get("player_role"),
                        "success": step.get("success"),
                        "artifacts": ", ".join((step.get("artifacts") or {}).keys()),
                        "error": step.get("error"),
                    }
                    for step in step_results
                ],
                width='stretch',
                hide_index=True,
            )

        with st.expander("Raw execution data"):
            st.json(execution_details(result))


def main() -> None:
    """Render the metadata generation Streamlit page."""
    with st.container(border=True):
        data_control_col, standard_control_col = st.columns(2, gap="large")
        with data_control_col:
            uploaded_file = render_upload_control()
        with standard_control_col:
            standard_name = render_standard_control()

        data_preview_col, standard_preview_col = st.columns(2, gap="large")
        with data_preview_col:
            if uploaded_file is not None:
                file_bytes = uploaded_file.getvalue()
                render_preview(uploaded_file, file_bytes)
            else:
                file_bytes = None
                st.caption("Upload a CSV or TSV file to preview its data.")
        with standard_preview_col:
            render_standard_preview(standard_name)

    if uploaded_file is None or file_bytes is None:
        st.stop()

    file_key = uploaded_file_key(file_bytes, standard_name)

    if "metadata_generation_results" not in st.session_state:
        st.session_state.metadata_generation_results = {}

    active_job = st.session_state.get("metadata_generation_job")
    poll_generation = (
        active_job is not None
        and active_job["file_key"] == file_key
        and active_job["process"].is_alive()
    )

    @st.fragment(run_every=0.5 if poll_generation else None)
    def generation_controls() -> None:
        job = st.session_state.get("metadata_generation_job")
        if job is not None and job["file_key"] != file_key:
            stop_generation(job)
            st.session_state.metadata_generation_job = None
            job = None

        running = job is not None and job["process"].is_alive()
        result = st.session_state.metadata_generation_results.get(file_key)
        metadata = extract_metadata(result) if result is not None else None
        export_disabled = not metadata or running
        download_disabled = (
            export_disabled
            or st.session_state.get(RESULT_TAB_KEY, "Metadata") != "Metadata"
        )
        action_col, _, format_col, download_col = st.columns(
            [1.4, 1, 0.8, 0.8], gap="small"
        )
        with action_col:
            clicked = st.button(
                "Stop generation" if running else "Generate metadata",
                type="primary",
                width='stretch',
            )
        with format_col:
            export_format = st.selectbox(
                "Export format",
                options=EXPORT_FORMATS,
                label_visibility="collapsed",
                disabled=download_disabled,
                key="metadata_export_format",
            )
        extension, mime_type = EXPORT_FORMATS[export_format]
        serialized_metadata = (
            serialize_metadata(metadata, export_format) if metadata else ""
        )
        with download_col:
            st.download_button(
                "Download",
                data=serialized_metadata,
                file_name=f"{Path(uploaded_file.name).stem}_metadata_gen.{extension}",
                mime=mime_type,
                disabled=download_disabled,
                width="stretch",
            )

        if clicked and running:
            stop_generation(job)
            st.session_state.metadata_generation_job = None
            st.rerun()

        if clicked and not running:
            st.session_state.metadata_generation_error = None
            context = multiprocessing.get_context("spawn")
            messages = context.Queue()
            process = context.Process(
                target=run_generation,
                args=(uploaded_file.name, file_bytes, standard_name, messages),
                daemon=True,
            )
            process.start()
            job = {
                "file_key": file_key,
                "process": process,
                "messages": messages,
                "progress": [],
                "started_at": monotonic(),
            }
            st.session_state.metadata_generation_job = job
            st.rerun()

        if job is not None:
            while True:
                try:
                    message = job["messages"].get_nowait()
                except Empty:
                    break
                if message[0] == "progress":
                    job["progress"].append(message[1:])
                    job["last_progress_received_at"] = monotonic()
                elif message[0] == "result":
                    st.session_state.metadata_generation_results[file_key] = message[1]
                    job["process"].join(timeout=1)
                    st.session_state.metadata_generation_job = None
                    st.rerun()
                elif message[0] == "error":
                    st.session_state.metadata_generation_job = None
                    st.session_state.metadata_generation_error = (
                        file_key,
                        f"Metadata generation failed: {message[1]}",
                    )
                    st.rerun()

            stages = {stage for stage, _, _ in job["progress"]}
            current_stage = (
                job["progress"][-1][0] if job["progress"] else None
            )
            if job["progress"]:
                elapsed = job["progress"][-1][2] + (
                    monotonic() - job["last_progress_received_at"]
                )
            else:
                elapsed = monotonic() - job["started_at"]
            status = st.status(
                (
                    f"{CURRENT_STAGE_LABELS.get(current_stage, 'Preparing metadata generation')}"
                    f" · {elapsed:.1f}s"
                ),
                expanded=True,
                state="running",
            )
            event_times = {
                stage: event_elapsed
                for stage, _, event_elapsed in job["progress"]
            }
            for label, active_stage, completed_stage in GENERATION_STEPS:
                started_at = event_times.get(active_stage)
                completed_at = event_times.get(completed_stage)
                if completed_stage in stages and started_at is not None:
                    marker = "✓"
                    duration = completed_at - started_at
                elif active_stage in stages and started_at is not None:
                    marker = "⏳"
                    duration = elapsed - started_at
                else:
                    marker = "○"
                    duration = None
                suffix = f" · {max(0.0, duration):.1f}s" if duration is not None else ""
                status.write(f"{marker} {label}{suffix}")

            for stage, payload, _ in job["progress"]:
                if stage == "context_created":
                    with status.expander("Context"):
                        st.json(payload)
                elif stage == "plan_generated":
                    with status.expander("Generated plan"):
                        st.json(payload)
            if not job["process"].is_alive():
                st.session_state.metadata_generation_job = None
                st.session_state.metadata_generation_error = (
                    file_key,
                    "Metadata generation stopped unexpectedly.",
                )
                st.rerun()
            return

        generation_error = st.session_state.get("metadata_generation_error")
        if generation_error is not None and generation_error[0] == file_key:
            st.error(generation_error[1])

        if result is None:
            st.caption("Click Generate metadata to run the agent.")
        else:
            render_result(result)

    with st.container(border=True):
        generation_controls()


if __name__ == "__main__":
    main()
