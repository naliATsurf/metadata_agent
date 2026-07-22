# Change log 2026-07-22 — Evidence caller attribution; player prompt cleanup; survey feed dedup

**Goal:** Three changes, prompted by reading the `evidence_*.json` output and
asking three questions of it. First: the ledger records *what* each tool
produced but not *who* asked or at which step, and deduplication was quietly
erasing that — a fact requested by the orchestrator's inspect pass and several
players is stored once, so a single caller field could not represent it. Second:
the player's reasoning prompt drew its values from two mechanisms (f-string-baked
into the template, and template placeholders filled at invoke), which was both
inconsistent and a latent crash. Third: every player is handed the whole survey,
and ~64% of it is three tools describing the same context — overkill that floods
the prompt. Each is addressed below.

## Caller attribution — the ledger now records who asked

A tool call is captured at one boundary (`tools/base._build_llm_facing_function`),
and until now the captured entry was the raw `(tool, resource, args, result)`.
That answers "what facts does this run stand on," but not "which player, at which
step, relied on this." The two are different audit questions and the file only
answered the first.

The reason the caller was absent is not an oversight — it is a consequence of the
design. Facts are **deduplicated** by `(tool, resource, args)` via `_RESULT_CACHE`:
every player surveys the context independently over a shared toolset, so the same
call recurs many times but is produced (and recorded) once. There is therefore no
single caller to attribute a fact to — the orchestrator inspect pass and three
players may all have "produced" it. So the caller cannot be a field on the fact;
it has to be a **list of uses**.

**`used_by`.** Each `EvidenceEntry` now carries an ordered `used_by` list. The
first entry is the producer (`cached=False`); each later one is a cache hit
(`cached=True`) — the same fact reused by a subsequent step or player without
re-running the tool. This preserves deduplication (one stored fact, replayable as
before) while restoring the call graph deduplication was erasing.

**`Caller` + `attributed_to`.** Who is firing tools right now is carried
ambiently in a `contextvars` scope, not threaded through tool signatures. That is
forced by the boundary: tools are reached through LangChain's `tool.invoke` and
the model's own tool-calling loop, neither of which we can add a parameter to.
Each phase enters a scope around the tools it fires — `orchestrator`/`inspect`,
`player`/`survey`, `player`/`investigate` — and the capture site reads it. The
step index reaches the player from `StepExecutor` via a new `execute_task(...,
step_index=...)` argument.

**Recorded on both paths.** `record_evidence` notes the producing use; a new
`record_reuse` notes each cache hit. The result cache stores `(result, entry)` so
a hit can find the entry to append to. This is the crux: without recording reuse,
every player after the first would vanish from the trace.

`used_by` serializes through `serialize_evidence` into `final_evidence` and the
`evidence_*.json` file, and `ExecutionResult.final_evidence` documents it.

*Caveat, recorded so it is not mistaken for a bug:* the orchestrator's inspect
pass runs under a throwaway `inspect_*` context key that is unregistered before
output, so its evidence never reaches the file. In the output, each surveyed
fact's producer is therefore the **first player** to survey it, not the
orchestrator. The orchestrator scope is still correct for that sweep's own
(discarded) ledger.

Files: `src/provenance.py`, `src/tools/base.py`,
`src/orchestrator/orchestrator.py`, `src/players/player.py`,
`src/orchestrator/step_executor.py`, `src/core/schemas.py`.

## Player prompt — one source of truth, and a latent crash removed

`Player.execute_task` built its reasoning prompt from values in two places:
`name`, `role_prompt`, `tool_descriptions`, and `ctx_info` were f-string-baked
into the template text at construction; `task`, `target_resources`, and
`input_context` were `{placeholder}` variables filled at `invoke`. The split
bought nothing — the template is rebuilt on every call, so there is no reuse to
gain by baking values in early.

Worse, it was a latent crash. `ChatPromptTemplate` uses f-string format, so any
`{`/`}` in the baked-in text is parsed as a variable. A tool description or role
prompt containing a brace — an example dict, say — would be misread as a
placeholder and raise `Missing variables` at runtime. Values passed through
`invoke` do not have this problem, which is exactly why `tool_results` (a dict
repr full of braces) was already safe on that path.

**Fix.** Every dynamic value is now a template variable, defined once in a single
`prompt_vars` dict and filled at invoke. All variable definitions live in one
place, and all of them travel the brace-safe path. While consolidating, the
previous-step inputs and the tool results — previously concatenated into one
`input_context` blob — were split into two separate labeled variables
(`input_context`, `tool_results`) with their own sections in the prompt.

Files: `src/players/player.py`.

## Survey feed dedup — stop feeding three copies of the same context

Measured on a real run (`evidence_all.trait.predictions_20260721_135418.json`),
each player's prompt carried ~3,800 tokens of survey results, and **64% of it was
three tools describing the same thing**: `get_context_schema` and
`get_resource_info` are strict *leaf-subsets* of `get_context_overview` (73/73
and 71/71 leaves contained), while a genuinely distinct tool like
`get_field_statistics` shares 0/78. So the redundancy is exact and detectable
without naming any tool.

**`_drop_subsumed_results`.** Before the survey is fed to the player's prompt, a
result is dropped when every one of its scalar leaves also appears in a larger
result — it then adds nothing the player cannot already see in that larger one.
Value-first, not name-based, so it fits the capability-driven architecture and
any future redundant describer collapses automatically. A `min_leaves` guard
keeps small results whose containment would be coincidental (a lone `0` from a
missing-value count, a resource name), where a shared leaf does not mean the fact
is genuinely restated.

Only the **prompt feed** is curated. The evidence ledger and every downstream
consumer still see the full, untrimmed `tool_results`; this changes what floods
the model, nothing about what is recorded or attributed.

On the measured run this cut the per-player payload ~42% (12 → 10 tools; ~3,856
→ ~2,239 tokens), dropping exactly `get_context_schema` and `get_resource_info`
with no loss of grounding — every fact they carried remains in the overview the
player still sees.

Files: `src/players/player.py`.

## Verification

- `pytest` — **99 pass, 1 skip** (was 91 pass, 1 skip before the session), the
  1 warning pre-existing (`@validator` deprecation in `schemas.py`).
- New tests:
  - `tests/test_provenance.py::CallerAttributionTest` — producing call records
    its caller; a cache hit by a *different* caller appends a reuse to the same
    fact; a call outside any scope is attributed to `unknown`; `used_by` survives
    serialization.
  - `tests/test_player_tool_dispatch.py::SubsumedResultDedupTest` — a strict
    subset describer is dropped; a result with distinct facts is kept; a small
    contained result is kept (coincidental containment); two identical results
    keep exactly one.
- `test_serialize_evidence_is_plain_dicts` updated for the new `used_by` key.
- The dedup helper was run directly against the real evidence file to confirm the
  42% figure and that only the two intended describers are dropped.

## Scope and limits

- **The dedup and prompt-feed truncation interact.** `_drop_subsumed_results`
  assumes the overview is fed *whole* — anything subsumed by it is dropped
  because the overview still carries those facts. A later change that truncates
  survey results must cap the long-tail results, not gut the overview the dropped
  describers now rely on. Order matters when both land.
- **`min_leaves=20` is the one tunable knob.** It has wide margin on this data
  (describers ~71 leaves, small tools ≤11), but a future modality producing
  mid-size results may need it revisited.
- **Caller attribution is not yet visible for the inspect pass** — see the caveat
  above. Making it visible would mean reusing the run's `ctx_*` key for inspect
  rather than a throwaway key, which is a separate change with its own
  implications.
- **The field-router / `Searchable` design discussed this session is not
  included here** — it is a proposal, not a shipped change. It belongs in a plan
  document (alongside `plan_free_text.md` / `plan_multi_modality.md`) if pursued.
