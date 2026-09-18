# Change log 2026-09-18 — Every single-call LLM role is registered in one place, and every prompt is published

**Goal:** Five LLM roles had grown across two layers — prose reader, claim comparer,
tool matcher, column matcher, passage reader — each with its own prompt constant, its own
referee and its own model setting, and nothing listed them. The prompt reference page
documented the orchestrator and the players but none of these, so the only way to read a
prompt was to open the module. And the settings panel offered two model settings without
saying what they drove.

Not the players' abstraction: these take no tools, keep no memory and decide nothing
about what runs next. What they needed was a name and a list, not autonomy.

## Change

- **`src/llm_roles.py`** registers each role with its question, layer, model setting, the
  class that calls it, how its calls are batched, what code checks about the answer, and
  the live prompt. `roles(module)` gives the roles one model setting drives.
- **Prompts are public constants** (`PROMPT`, `PROSE_READER_PROMPT`,
  `CLAIM_COMPARER_PROMPT`) rather than private ones, since the registry and the docs page
  both read them. They stay in the module that sends and referees them: a prompt read
  apart from its referee is half the contract.
- **The prompt reference** (`docs/_ext/promptdocs.py`) gains a **Single-call roles**
  section: a table of every role, then each one's prompt, its placeholders, how its calls
  are shaped and what the referee checks. Generated at build, so it cannot drift.
- **The settings panel** names the roles behind each model setting ("Runs: Tool matcher,
  Column matcher, Passage reader"), which is what makes one setting for three roles
  legible.
- **`tests/test_llm_roles.py`** fails when an `LLM*` class in `src/router` is missing from
  the registry, when a role names a model setting that does not exist, or when the
  generated page drops a prompt.

## Not done

The shared plumbing these five still duplicate — two JSON extractors, three caches, a
per-class `from_chat_model`, a debug-logging wrapper copied into both example scripts —
is untouched. The registry makes it visible; extracting a common base is a separate
change.
