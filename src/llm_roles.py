"""Every single-call LLM role in the pipeline, named in one place.

A **role** here is one call with a fixed prompt: no tools, no memory, no say over what
runs next. It is asked a narrow question, answers in JSON, and code referees the answer
before anything downstream uses it — a model proposes, code disposes. The other kind of
LLM in this repo is the tool-using :class:`~src.players.player.Player`, whose personas
live in ``src/players/configs.py``; both are documented on the generated prompt
reference page.

This registry is the one place a role's question, model settings, prompt and referee are
stated together. Three things read it, which is what keeps it honest:

- the generated prompt reference (``docs/_ext/promptdocs.py``) publishes every prompt
  below, so a prompt cannot drift from its documentation;
- the settings panel names the roles each model setting drives;
- a test fails when an LLM-backed class in the pipeline is missing from it, so a new
  role cannot be added invisibly.

The prompt text itself stays next to the code that parses and referees it: a prompt read
apart from its referee is half the contract. What lives here is the pointer to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from src.config import LLM_MODULES
from src.router import catalog, column_matcher, passage_reader, tool_matcher

CATALOG_LAYER = "Catalog resolver (3)"
ROUTER_LAYER = "Field router (4b)"


@dataclass(frozen=True)
class Role:
    """One LLM role: what it is asked, what answers it, and what checks the answer."""

    key: str
    title: str
    layer: str
    decides: str            # the question it answers, in one sentence
    module: str             # which LLM_MODULES entry its provider/model/temperature come from
    implemented_by: str     # the class that makes the call
    calls: str              # how the calls are shaped — what one call covers
    checked: str            # what code does with the answer before anything uses it
    prompt: str             # the live template, as sent (with its {placeholders})

    @property
    def settings_label(self) -> str:
        """How the settings panel names this role's model."""
        return LLM_MODULES[self.module].label


ROLES: Tuple[Role, ...] = (
    Role(
        key="prose_reader",
        title="Prose reader",
        layer=CATALOG_LAYER,
        decides="What a document says a column means, for columns no codebook explains.",
        module="CATALOG_RESOLVER",
        implemented_by="src.router.catalog.LLMProseReader",
        calls="One per passage, asked about every unexplained column at once.",
        checked=(
            "The quote must be found in the document or the read is graded low and "
            "flagged; the column's value profile can refute the claim; a column the "
            "answer omits abstains."
        ),
        prompt=catalog.PROSE_READER_PROMPT,
    ),
    Role(
        key="claim_comparer",
        title="Claim comparer",
        layer=CATALOG_LAYER,
        decides="Which of a column's differently worded descriptions mean the same thing.",
        module="CATALOG_RESOLVER",
        implemented_by="src.router.catalog.LLMClaimComparer",
        calls="One per bundle, covering every column whose claims differ.",
        checked=(
            "An answer that is not a partition of that column's claims falls back to "
            "identical wording, so in doubt claims disagree rather than corroborate."
        ),
        prompt=catalog.CLAIM_COMPARER_PROMPT,
    ),
    Role(
        key="tool_matcher",
        title="Tool matcher",
        layer=ROUTER_LAYER,
        decides="Which schema fields a tool computes, seeing the tools but no data.",
        module="CANDIDATE_JUDGE",
        implemented_by="src.router.tool_matcher.LLMToolMatcher",
        calls=(
            "Fields in even groups of at most `router_max_fields_per_call`. Answers are "
            "cached on disk, so a standard costs calls on its first bundle only."
        ),
        checked=(
            "A tool it names that was not shown is discarded; the table and columns the "
            "tool needs come from the column matcher, and a tool left missing one does "
            "not run."
        ),
        prompt=tool_matcher.PROMPT,
    ),
    Role(
        key="column_matcher",
        title="Column matcher",
        layer=ROUTER_LAYER,
        decides=(
            "Which column answers each field, and which table or column each chosen "
            "tool's argument takes."
        ),
        module="CANDIDATE_JUDGE",
        implemented_by="src.router.column_matcher.LLMColumnMatcher",
        calls=(
            "One per catalog slice per field group: the whole catalog at once unless it "
            "exceeds `router_match_max_chars`."
        ),
        checked=(
            "A ref that was not shown is discarded; a table named for a field is not an "
            "answer to it; type and unit fit are graded afterwards and cap the routing."
        ),
        prompt=column_matcher.PROMPT,
    ),
    Role(
        key="passage_reader",
        title="Passage reader",
        layer=ROUTER_LAYER,
        decides="Whether a passage states a field's value, and which sentences say so.",
        module="CANDIDATE_JUDGE",
        implemented_by="src.router.passage_reader.LLMPassageReader",
        calls="One per passage per field group — each passage read once for many fields.",
        checked=(
            "Every quote must be found in the passage, or the answer is capped at low "
            "and marked ungrounded; a located quote becomes the routing's citation."
        ),
        prompt=passage_reader.PROMPT,
    ),
)


def roles(module: str | None = None) -> Tuple[Role, ...]:
    """Every role, or the ones one :data:`~src.config.LLM_MODULES` entry drives."""
    return tuple(r for r in ROLES if module is None or r.module == module)


def role(key: str) -> Role:
    """One role by key."""
    return next(r for r in ROLES if r.key == key)
