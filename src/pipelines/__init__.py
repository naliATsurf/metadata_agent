"""Pipelines: how the layers are put together, and nothing else.

A layer (:mod:`src.router`, :mod:`src.orchestrator`, :mod:`src.tools`) owns an
algorithm. A pipeline module here owns an *order*: which stage runs, on what, with which
model-backed roles, producing which artifact. That split is the rule this package lives
by — anything here that starts deciding rather than composing belongs in a layer.

Each stage is importable on its own, so a caller can run part of a path:

===================================  ===========================================
:mod:`~src.pipelines.catalog`        bundle files → a resolved catalog (3)
:mod:`~src.pipelines.routing`        catalog → field plan (4) → executable plan (5)
:mod:`~src.pipelines.field_driven`   the whole field-driven path, composed
:mod:`~src.pipelines.models`         the model-backed roles a stage runs with
===================================  ===========================================

The command-line surface over these lives in :mod:`src.cli`, and ships with the library:
the scripts in ``examples/`` are thin wrappers around it, not the place the pipeline is
assembled.
"""

from src.pipelines.catalog import DEFAULT_BUNDLE, resolve, resolve_directory
from src.pipelines.field_driven import FieldDrivenRun, run
from src.pipelines.models import (
    Judges,
    Readers,
    build_judges,
    build_readers,
    invoker,
)
from src.pipelines.routing import (
    DEFAULT_STANDARD,
    compile_plan,
    route,
    route_and_compile,
)

__all__ = [
    "DEFAULT_BUNDLE",
    "DEFAULT_STANDARD",
    "FieldDrivenRun",
    "Judges",
    "Readers",
    "build_judges",
    "build_readers",
    "compile_plan",
    "invoker",
    "resolve",
    "resolve_directory",
    "route",
    "route_and_compile",
    "run",
]
