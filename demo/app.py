"""Streamlit entry point for the metadata agent demo.

Holds the page configuration and the navigation. Pages are plain ``main()``
functions, so each stays runnable on its own and none of them owns app-wide
setup.
"""

import streamlit as st

st.set_page_config(page_title="Metadata Agent", page_icon="MD", layout="wide")

from demo.pages import metadata_generation, modules  # noqa: E402


def main() -> None:
    """Build the navigation and run the selected page."""
    navigation = st.navigation(
        [
            st.Page(
                metadata_generation.main,
                title="Metadata generation",
                url_path="generation",
                default=True,
            ),
            st.Page(modules.main, title="Modules", url_path="modules"),
        ]
    )
    navigation.run()


if __name__ == "__main__":
    main()
