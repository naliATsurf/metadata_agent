# Extending

Four things people usually add: a standard, a tool, a judge, a model. Each has one place
to add it, and the rest of the pipeline picks it up.

## A metadata standard

Add an entry to `STANDARD_DEFINITIONS` in `src/standards.py`. Everything else is derived
from it at import — the Pydantic schema, the command's `--standard` choices, the app's
dropdown:

```python
STANDARD_DEFINITIONS["my_standard"] = {
    "title": {
        "type": str,
        "default": ...,                      # ... means required; None means optional
        "description": "title of the dataset, as the authors wrote it",
        "prompt_hint": "The dataset title",
    },
    "sample_size": {
        "type": Optional[str],
        "default": None,
        "description": "number of individuals measured",
        "prompt_hint": "How many individuals were measured",
    },
}
```

**The description is the routing query.** It is what the column matcher and the passage
reader are shown, so write it as the question you want answered — "number of individuals
measured" routes; "sample size" does not.

Then:

```bash
metadata-agent route --catalog catalog.json --standard my_standard
```

A standard that derives from another one stays in step with it:

```python
STANDARD_DEFINITIONS["my_subset"] = {
    name: spec for name, spec in STANDARD_DEFINITIONS["sharetrait_basic"].items()
    if not name.startswith("trait_")
}
```

## A tool that answers a field

A tool whose result *is* a metadata value can be routed to. Declare it once and the
router does the rest:

```python
from src.context.base_context import TabularContext
from src.tools.base import ColumnArg, context_tool

@context_tool(
    toolset="tabular.temporal",
    requires=TabularContext,             # capability gating: what it can run on
    answers_field=True,                  # its result is a metadata value
    column_args={                        # the columns it must be given
        "time_column": ColumnArg("the date each record was taken", "temporal"),
    },
)
def get_season_span(ctx: TabularContext, resource: str, time_column: str) -> dict:
    """The seasons a timestamp column covers."""
    ...
```

What happens then, without any further wiring:

1. the **tool matcher** is shown the tool and decides which fields it computes — it sees
   the description and the `column_args`, never the data;
2. the **column matcher** is asked which column fills `time_column`, as one more line in
   its field list;
3. **code** binds them: every argument answered, and the argument's column checked against
   `values="temporal"`. A tool missing an argument does not run, and the field falls back.

`answers_field=True` with an undeclared argument fails at import, so a tool cannot quietly
become unroutable.

## A judge that is not a model

Every judge is an interface. Implement the method and hand it in:

```python
from src.pipelines import Judges, route
from src.router.column_matcher import ColumnMatcher
from src.router.judge import Verdict

class NameMatcher(ColumnMatcher):
    """A field is answered by a column its path names."""

    def match(self, *, fields, cards):
        verdicts = {}
        for field in fields:
            words = set(field.path.lower().split("_"))
            hit = next((c for c in cards if str(c.get("column", "")).lower() in words), None)
            verdicts[field.path] = (
                Verdict(choice=hit["ref"], because="the field path names this column",
                        confidence="medium")
                if hit else Verdict(choice=None, because="no column of that name")
            )
        return verdicts

field_plan = route(resolved, "sharetrait_basic_no_trait", judges=Judges(columns=NameMatcher()))
```

The same applies to `ToolMatcher.match` and `PassageReader.read` / `read_many`. A runnable
version of the above, with a stubbed passage reader beside it, is
`examples/route_with_your_own_judge.py`.

**Or replace the model call, not the judge.** Every LLM-backed role takes a plain
`prompt -> text` callable, so logging, caching, recording fixtures and testing all hang
there:

```python
from src.router.column_matcher import LLMColumnMatcher

def cached(invoke, store: dict):
    def call(prompt: str) -> str:
        if prompt not in store:
            store[prompt] = invoke(prompt)
        return store[prompt]
    return call

matcher = LLMColumnMatcher(cached(my_model, {}))
```

## A model, or another provider

Per-module settings in `.env` decide what each role runs on:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_MODEL_CANDIDATE_JUDGE=gpt-4o          # the router's three judges
LLM_MODEL_CATALOG_RESOLVER=gpt-4o-mini    # the prose reader and claim comparer
```

Or per run:

```python
from src.pipelines import build_judges

judges = build_judges(provider="google", model="gemini-2.5-flash", temperature=0.0)
```

A provider that is not one of the three is a callable away — build the client yourself and
pass its `invoke` to the roles, as in the caching example above. Nothing in the pipeline
depends on a provider SDK.

New roles belong in `src/llm_roles.py`: the registry is what the settings panel and the
generated [Prompt reference](../prompts.md) read, and a test fails when an LLM-backed
class in the router is missing from it.

## A threshold

Add a field to `Thresholds` in `src/thresholds.py` with its stage, label, help and
default. It is then settable by `THRESHOLD_<NAME>` in `.env`, appears in the app's
settings panel, and is readable with `thresholds.current()` — no other wiring.

## Testing what you added

The deterministic path needs no model, and every judge takes a stub, so tests stay fast
and offline:

```python
import json
from src.router.column_matcher import LLMColumnMatcher

def scripted(reply):
    def invoke(prompt: str) -> str:
        return reply
    return invoke

matcher = LLMColumnMatcher(scripted(json.dumps({"title": {"choice": None}})))
```

Useful fixtures: `src.tools.base.clear_registry()` between tests that register tools, and
`thresholds.use(...)` to scope a threshold to a block.

## Measuring whether it helped

Label a bundle once, then grade any configuration against it:

```bash
python -m eval sheet                        # writes labels.csv to fill in by hand
python -m eval score --llm-candidate-judge  # grades the current router
```

The metrics are in `eval/readme.md`. Watch over-answering: a change that answers more
fields is not an improvement unless the answers are right, and that is the number that
moves when a prompt gets looser.
