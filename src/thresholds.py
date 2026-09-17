"""Every tunable threshold of the catalog resolver, the field router and the plan compiler.

These numbers used to be module constants scattered across the layers they tune, which
made them invisible to anyone not reading that module and impossible to change without
editing code. They are gathered here as one value, :class:`Thresholds`, so that a
threshold is found in one place, documented once, and set the same way everywhere.

A value comes from, in order of precedence:

1. an override in force for the current run — :func:`use`, which is how the app's
   settings panel applies its values to a run without touching the process
   environment;
2. the environment, ``THRESHOLD_<NAME>`` (``.env`` is loaded), e.g.
   ``THRESHOLD_ROUTER_READ_ALL_MAX_PASSAGES=20``;
3. the default declared below.

Code reads :func:`current` **when it needs a value**, never at import time, so an
override or a changed environment takes effect on the next call.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterator, List, Optional

from dotenv import load_dotenv

load_dotenv()

#: The environment variables a threshold is read from are this plus its upper-cased name.
ENV_PREFIX = "THRESHOLD_"

CATALOG = "Catalog resolver"
ROUTER = "Field router"
COMPILER = "Plan compiler"


def _threshold(default: Any, stage: str, label: str, help: str, minimum: Any = 0) -> Any:
    return field(
        default=default,
        metadata={"stage": stage, "label": label, "help": help, "min": minimum},
    )


@dataclass(frozen=True)
class Thresholds:
    """The numbers the pipeline decides by. See each field's ``help`` for its effect."""

    # -- catalog resolution (layer 3) -----------------------------------------

    catalog_profile_sample: int = _threshold(
        1000, CATALOG, "Rows profiled per table",
        "Rows sampled to compute each column's value profile. Approximate statistics are "
        "enough for a prior and for cross-checking a claim, and sampling keeps the cost "
        "following the schema rather than the row count.", 1,
    )
    catalog_dictionary_key_precision: float = _threshold(
        0.5, CATALOG, "Codebook key precision",
        "Share of a column's values that must be the table's column names for the column "
        "to key a codebook — and share of a glossary run's terms that must be. Lower "
        "accepts more partial codebooks; higher rejects more coincidences.",
    )
    catalog_dictionary_key_uniqueness: float = _threshold(
        0.9, CATALOG, "Codebook key uniqueness",
        "Share of a codebook key column's values that must be unique. It is what stops a "
        "table of repeated observations from being read as a codebook.",
    )
    catalog_text_codebook_min_entries: int = _threshold(
        3, CATALOG, "Glossary: fewest entries",
        "Adjacent `term = definition` entries needed before prose is treated as a "
        "glossary. Two side by side is ordinary in a Methods section; three is a list.", 1,
    )
    catalog_text_definition_max_chars: int = _threshold(
        160, CATALOG, "Glossary: longest definition (chars)",
        "A definition longer than this is a paragraph that happened to follow a "
        "separator, not a glossary entry.", 1,
    )
    catalog_whole_doc_max_chars: int = _threshold(
        20_000, CATALOG, "Read a document whole up to (chars)",
        "Documents up to this size go to the prose reader whole; longer ones are "
        "localized with retrieval first.", 1,
    )
    catalog_prose_read_k: int = _threshold(
        3, CATALOG, "Chunks retrieved per column",
        "In a long document, how many chunks each unresolved column retrieves before "
        "they are read.", 1,
    )
    catalog_passage_max_chars: int = _threshold(
        20_000, CATALOG, "Passage size (chars)",
        "Retrieved chunks are packed into passages up to this size, one reader call "
        "each.", 1,
    )
    catalog_grounding_support: float = _threshold(
        0.5, CATALOG, "Quote support for high confidence",
        "Share of a description's content words a located quote must contain, beside "
        "naming the column, for a prose read to be graded high rather than medium.",
    )

    # -- field routing (layer 4) ----------------------------------------------

    router_passage_max_chars: int = _threshold(
        20_000, ROUTER, "Passage size (chars)",
        "Each document is packed into contiguous passages up to this size; the passage "
        "reader reads one per call.", 1,
    )
    router_read_all_max_passages: int = _threshold(
        10, ROUTER, "Read every passage up to (passages)",
        "With the passage reader on, every passage is read for every unanswered field "
        "while the bundle has at most this many passages. Beyond it, BM25 chooses each "
        "field's passages — cheaper, but a passage that states a field without sharing "
        "its words is then never read for it.", 0,
    )
    router_match_max_chars: int = _threshold(
        30_000, ROUTER, "Column matcher catalog size (chars)",
        "A catalog larger than this is split into slices, one matcher call each, every "
        "slice with the full field list.", 1,
    )
    router_max_fields_per_call: int = _threshold(
        20, ROUTER, "Fields per judge call",
        "Most fields one column-matcher or passage-reader call is asked about; fields are "
        "divided evenly across calls. Fields judged together stop being independent, so "
        "higher saves calls and risks answers being spread across fields.", 1,
    )

    # -- plan compilation (layer 5) -------------------------------------------

    compile_task_budget_chars: int = _threshold(
        2000, COMPILER, "Task candidate budget (chars)",
        "Characters of seeded candidate evidence one extraction task may carry; a group "
        "over it is split into several tasks.", 1,
    )

    @classmethod
    def from_environment(cls) -> "Thresholds":
        """Defaults, overridden by any ``THRESHOLD_<NAME>`` set in the environment."""
        values: Dict[str, Any] = {}
        for spec in fields(cls):
            raw = os.getenv(env_name(spec.name))
            if raw is None or not raw.strip():
                continue
            kind = type(spec.default)
            try:
                values[spec.name] = kind(raw.strip())
            except ValueError as error:
                raise ValueError(
                    f"{env_name(spec.name)}={raw!r} is not a valid {kind.__name__}"
                ) from error
        return cls(**values)

    def environment(self) -> Dict[str, str]:
        """These values as the environment variables :meth:`from_environment` reads."""
        return {env_name(spec.name): str(getattr(self, spec.name)) for spec in fields(self)}

    def to_dict(self) -> Dict[str, Any]:
        return {spec.name: getattr(self, spec.name) for spec in fields(self)}


def env_name(name: str) -> str:
    """The environment variable a threshold is read from."""
    return f"{ENV_PREFIX}{name.upper()}"


def specs(stage: Optional[str] = None) -> List[Any]:
    """The threshold fields, optionally one stage's, for a UI to render."""
    return [s for s in fields(Thresholds) if stage is None or s.metadata["stage"] == stage]


_OVERRIDE: ContextVar[Optional[Thresholds]] = ContextVar("thresholds_override", default=None)


def current() -> Thresholds:
    """The thresholds in force: a :func:`use` override, else the environment's."""
    return _OVERRIDE.get() or Thresholds.from_environment()


@contextmanager
def use(thresholds: Thresholds) -> Iterator[Thresholds]:
    """Run a block with ``thresholds`` in force, whatever the environment says.

    Scoped to the current context, so it reaches the threads a judge dispatches its
    calls on (they run in a copy of the caller's context) and nothing else.
    """
    token = _OVERRIDE.set(thresholds)
    try:
        yield thresholds
    finally:
        _OVERRIDE.reset(token)
