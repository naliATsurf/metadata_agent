"""Streamlit entry point for the metadata agent demo.

Holds the page configuration and the navigation. Pages are plain ``main()``
functions, so each stays runnable on its own and none of them owns app-wide
setup.
"""

import streamlit as st

from demo.pages import metadata_generation, modules


# Wide mode still caps the content column, and these pages are tables and forms that
# read better across the full window. Several selectors, because the container's test
# id and class have both been renamed across Streamlit versions and a rule that misses
# is silent.
_PAGE_STYLE = """
<style>
  /* Multiselect chips list what is currently selected — they are not a call to
     action and should not carry the accent colour. Streamlit's own theme variables
     keep this correct in both light and dark mode. */
  [data-testid="stMultiSelect"] [data-baseweb="tag"] {
      background-color: var(--secondary-background-color, #e9ecef) !important;
      color: var(--text-color, inherit) !important;
  }
  [data-testid="stMultiSelect"] [data-baseweb="tag"] svg { fill: currentColor !important; }

  [data-testid="stMainBlockContainer"],
  [data-testid="stAppViewBlockContainer"],
  section.main > div.block-container,
  .block-container {
      max-width: 100% !important;
      padding-left: 3rem !important;
      padding-right: 3rem !important;
      /* Top padding is deliberately NOT overridden. Streamlit's default clears the
         fixed header bar; anything smaller slides the first element underneath it,
         which reads as the top control being cut in half. */
  }
</style>
"""


def main() -> None:
    """Configure the app, then build the navigation and run the selected page.

    Everything here runs on *every* script run, deliberately. Streamlit re-executes
    the entry script on each interaction but keeps imported modules cached, so
    configuration placed at module level would apply once to the first session in the
    process and silently not at all to any later one.
    """
    st.set_page_config(page_title="Metadata Agent", page_icon="MD", layout="wide")
    st.html(_PAGE_STYLE)

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
