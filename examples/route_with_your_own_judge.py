"""Example: route a standard with judges that are not models.

Every LLM role in the pipeline (see :mod:`src.llm_roles`) hangs on one of two seams, and
this routes a real bundle through both without a model, a key, or a call:

1. **The role itself.** A column matcher is anything with a ``match`` method, so
   ``NameMatcher`` below is a judge written in code — it answers from column names. Swap
   it for your own retrieval, a local classifier, a lookup table.
2. **The model call.** An LLM-backed role takes a plain ``prompt -> text`` callable, so
   ``watching`` below answers the passage reader itself while printing what it was asked.
   That is the seam the tests stub, and where you would hang logging, a cache or a
   recorded fixture.

What the run then shows is the routing itself: which fields a judge answered, which it
refused, and which nothing in the bundle can answer at all::

    python examples/route_with_your_own_judge.py
"""

from __future__ import annotations

import json
import os
import re
import sys

# Make the repo importable when run directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pipelines.catalog import DEFAULT_BUNDLE, resolve_directory
from src.pipelines.models import Judges
from src.pipelines.routing import route
from src.router.column_matcher import ColumnMatcher
from src.router.judge import Verdict
from src.router.passage_reader import LLMPassageReader

STANDARD = "sharetrait_basic_no_trait"


class NameMatcher(ColumnMatcher):
    """A judge that is code: a field is answered by a column its path names.

    Blunt on purpose — a real matcher weighs units, value ranges and meanings — but it
    is a complete implementation of the seam, and it costs nothing to run.
    """

    def match(self, *, fields, cards):
        verdicts = {}
        for field in fields:
            words = set(re.split(r"[^a-z0-9]+", field.path.lower()))
            hit = next(
                (card for card in cards
                 if str(card.get("column", "")).lower() in words),
                None,
            )
            verdicts[field.path] = (
                Verdict(choice=hit["ref"], because="the field path names this column",
                        confidence="medium")
                if hit else Verdict(choice=None, because="no column of that name")
            )
        return verdicts


def watching(name: str):
    """A ``prompt -> text`` callable that answers nothing, and reports what it was asked."""
    seen = []

    def invoke(prompt: str) -> str:
        seen.append(prompt)
        asked = [line for line in prompt.splitlines() if line.startswith("- ")]
        print(f"  [{name}] call {len(seen)}: {len(prompt):,} characters, "
              f"{len(asked)} field(s) asked about")
        return json.dumps({})        # every field abstains: "this passage states none"

    invoke.seen = seen
    return invoke


def main() -> None:
    resolved = resolve_directory(DEFAULT_BUNDLE)
    print(f"{DEFAULT_BUNDLE.name}: {len(resolved.catalog.columns)} columns resolved\n")

    reader = watching("passage reader")
    field_plan = route(
        resolved,
        STANDARD,
        judges=Judges(columns=NameMatcher(), passages=LLMPassageReader(reader)),
        documents=[p for p in resolved.documents if p.name == "readme.txt"],
    )

    coverage = field_plan.coverage()
    print(f"\n{STANDARD}: {coverage['routed']}/{coverage['total']} routed, "
          f"by bucket {coverage['by_bucket']}")

    print("\nAnswered by the name matcher:")
    for path, routing in field_plan.routings.items():
        if routing.bucket == "column":
            print(f"  {path:<28} {routing.candidates[0].resource}::"
                  f"{routing.candidates[0].locator} — {routing.judge_note}")

    print(f"\nUnanswered ({len(coverage['unanswered'])}): "
          f"{', '.join(coverage['unanswered'][:6])} …")
    print("\nThe reader was asked once per passage, for every field still unanswered — "
          f"{len(reader.seen)} call(s) here.\nReplace either judge with build_judges() "
          "to run the real ones.")


if __name__ == "__main__":
    main()
