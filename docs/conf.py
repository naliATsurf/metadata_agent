# docs/conf.py

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "_ext"))

project = "Metadata Agent"
# author = "Your Name"

master_doc = "index"
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

extensions = [
    "autoapi.extension",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_copybutton",
    "promptdocs",  # generates prompts.md from the live prompt templates
]

autoapi_type = "python"
autoapi_dirs = [str(ROOT / "src"), str(ROOT / "demo")]
autoapi_root = "reference"
autoapi_keep_files = False
autoapi_options = [
    "members",
    "show-module-summary",
    "imported-members",
]

# Generate `#slug` anchors for h1-h3 so pages can link to their own sections.
myst_heading_anchors = 3

# The prompt reference renders the templates' own ```json examples, which use
# ellipses and so aren't valid JSON. Pygments falls back to relaxed lexing and
# renders them fine; the warning is noise.
suppress_warnings = ["misc.highlighting_failure"]

napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_theme = "pydata_sphinx_theme"

html_theme_options = {
    "show_toc_level": 2,
    "navigation_with_keys": True,
    "show_prev_next": False,
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
