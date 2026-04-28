import sys
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.workflows.metadata_generation import (
    SUPPORTED_FILE_TYPES,
    available_metadata_standards,
    execution_details,
    extract_metadata,
    generate_metadata,
    load_preview,
    uploaded_file_key,
)


st.set_page_config(page_title="Metadata Generation", page_icon="MD", layout="wide")
st.title("Metadata Generation")

controls_col, preview_col = st.columns([1, 2], gap="large")

with controls_col:
    uploaded_file = st.file_uploader(
        "Upload a dataset",
        type=SUPPORTED_FILE_TYPES,
        help="CSV and TSV files are supported by the demo context.",
    )
    standard_name = st.selectbox(
        "Metadata standard",
        options=available_metadata_standards(),
        index=0,
    )

if uploaded_file is None:
    with preview_col:
        st.info("Upload a CSV or TSV file to start.")
    st.stop()

file_bytes = uploaded_file.getvalue()
file_key = uploaded_file_key(file_bytes, standard_name)

with preview_col:
    st.subheader("Data preview")
    try:
        st.dataframe(
            load_preview(uploaded_file.name, file_bytes),
            use_container_width=True,
        )
        st.caption(f"{uploaded_file.name} - {len(file_bytes):,} bytes")
    except Exception as exc:
        st.warning(f"Preview unavailable: {exc}")

if "metadata_generation_results" not in st.session_state:
    st.session_state.metadata_generation_results = {}

with controls_col:
    run_clicked = st.button("Generate metadata", type="primary", use_container_width=True)
cached_result = st.session_state.metadata_generation_results.get(file_key)

if run_clicked and cached_result is None:
    with preview_col:
        progress = st.status("Generating metadata", expanded=True)

    try:
        progress.write("File uploaded and staged for analysis.")
        progress.write("Running the metadata agent.")
        cached_result = generate_metadata(uploaded_file.name, file_bytes, standard_name)
        st.session_state.metadata_generation_results[file_key] = cached_result
        progress.update(label="Metadata generated", state="complete")
    except Exception as exc:
        progress.update(label="Metadata generation failed", state="error")
        st.exception(exc)
        st.stop()

if cached_result is None:
    with preview_col:
        st.caption("Click Generate metadata to run the agent.")
    st.stop()

metadata = extract_metadata(cached_result)

with preview_col:
    st.subheader("Generated metadata")
    if metadata:
        st.json(metadata)
    else:
        st.warning("No final metadata artifact was found in the result.")

    with st.expander("Execution details"):
        col1, col2, col3 = st.columns(3)
        col1.metric("Plan steps", cached_result.get("plan_steps_count", 0))
        col2.metric("Completed", cached_result.get("steps_completed", 0))
        col3.metric("Success", "Yes" if cached_result.get("success") else "No")

        if cached_result.get("error"):
            st.error(cached_result["error"])

        st.json(execution_details(cached_result))
