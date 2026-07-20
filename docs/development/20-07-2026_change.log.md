# Change log 2026-07-20 — Generated prompt reference; prompt de-duplication

**Goal:** Make the prompts documentable without keeping a second copy of them.
The docstrings in `src/orchestrator/prompts.py` restated each prompt in full so
Sphinx could render them, which meant every prompt existed twice with nothing
keeping the copies in sync — and they had already drifted. Replace that with a
docs page generated from the live prompts, then remove the duplicates. A repo
audit during the work turned up two further prompt sources and seven dead
prompts; both are addressed here.

## The drift was already real

The duplication was not hypothetical. Before this change:

- `get_single_csv_planning_prompt`'s docstring omitted the entire **Context
  Overview** section, the **Grounding rules** block, key instruction 5, and all
  of the one-artifact-per-`inputs`-value rules. It documented a prompt that had
  not existed for several commits.
- `get_multi_csv_planning_prompt`'s docstring described a 3-phase plan; the live
  template was 4 phases with a conditional spatial step.

A docstring that duplicates the body decays silently — nothing fails when it
goes stale. That is the argument for generating instead of restating.

## Generated reference — `docs/_ext/promptdocs.py`

A Sphinx extension on the `builder-inited` hook writes `docs/prompts.md` from
source at build time. Per prompt it emits the one-line summary, provenance, the
placeholder set, and each message body.

**Placeholders are derived, not written.** For the orchestrator prompts they come
from `ChatPromptTemplate.input_variables`, so they are correct by construction.
This is the check that would have caught the missing `{dataset_info}`: a
hand-written list can lose an entry, a derived one cannot.

**Three sources, two extraction strategies.** The orchestrator prompts are
zero-argument factories, so they are imported and introspected. The player
prompts are f-strings built inside methods from `self` and method locals — they
cannot be imported and called, so they are extracted statically with `ast`,
walking up to the enclosing method for a name and rendering interpolations as
`{expr}` to match how the orchestrator templates display theirs. Role personas
are read from `PLAYER_CONFIGS`.

**Message bodies render as markdown**, so the prompts' own bold, lists, and
fenced examples display as formatting rather than as a wall of literal text.
This is safe only while no prompt contains an ATX heading — one would silently
land in the page's table of contents — so `_assert_renderable` fails the build
with the offending prompt, role, and line if that ever changes. The tradeoff is
that the page no longer shows the *literal* characters sent to the model; a
collapsible verbatim view is a small change from here if that becomes a problem
while debugging a prompt.

Two rendering bugs found and fixed during the work, both recorded in comments
so they are not reintroduced: the prompts contain their own ``` fences, so a
literal-block wrapper has to be longer than any fence inside the text or it
closes early; and Pygments' `markdown` lexer errors on prompt prose.

## The audit — prompts live in three places, not one

Searching for `ChatPromptTemplate`, `SystemMessage`, and `role_prompt` across
the repo found prompt text in three modules:

| Location | What | Status found |
|---|---|---|
| `src/orchestrator/prompts.py` | 9 `get_*_prompt` factories | 2 live, 7 orphaned |
| `src/players/player.py` | 7 prompts built inline in methods | all live |
| `src/players/configs.py` | 7 `role_prompt` personas | all live |

**The seven orphans were stale copies of live prompts.** `get_critique_prompt`,
`get_revision_prompt`, `get_synthesis_prompt`, `get_task_execution_prompt`,
`get_initial_work_prompt` and the two deprecated aliases had no callers anywhere
in `src`, `demo`, `examples`, `tests`, or `notebooks`. Their live equivalents are
the inline prompts in `player.py`. Editing the orphans looked like tuning the
system and did nothing — the worst failure mode available, because it is silent.

## Deletions

**The seven orphans — 253 lines.** Verified unreferenced across `.py`, `.md`,
`.ipynb`, `.rst`, and `.toml`, with no dynamic access (`getattr`, `globals()`)
and no export from `src/orchestrator/__init__.py`, which does not surface
prompts at all. Removed by AST line range rather than by hand, absorbing trailing
blank lines so the spacing between the survivors stayed intact.

**The duplicated docstrings — 147 lines.** Both survivors now carry only what
cannot be recovered by reading the template beneath them: which context type
selects them, that the chain parses into `Plan`, the fallback that re-invokes
without `format_instructions` to log raw output on a parse failure, and the
final-step contract (`metadata_generator` → `["metadata_output"]`) that
`PlanExecutor` depends on to locate the result. The multi-CSV docstring
cross-references the single-CSV one and names only its three real differences.

The module docstring claimed to store *all* prompt templates for the system,
which stopped being true once the player prompts were found; it now points at
`src.players.player` and `src.players.configs`.

`src/orchestrator/prompts.py`: **607 → 239 lines**, 32 of them prose.

## Configuration

- `docs/conf.py`: registers the extension and adds `docs/_ext` to the path.
- `myst_heading_anchors = 3` — needed for the reference page's internal link.
  Side effect: it also resolved several pre-existing broken cross-references on
  other pages, taking the whole docs build from 13 warnings to 4.
- `suppress_warnings = ["misc.highlighting_failure"]` — rendering the prompts'
  own ` ```json ` examples makes them real code blocks, and they contain
  ellipses so Pygments cannot lex them as JSON. The ellipsis is deliberate
  prompt content; Pygments falls back to relaxed mode and renders correctly.
- `docs/prompts.md` is gitignored — it is a build product.

## Verification

- Both surviving templates hash-identical before and after the docstring edit
  (2 messages each; 5 and 7 placeholders) — prose only, no template touched.
- `pytest tests` — **91 pass, 1 skip**, unchanged across both deletions.
- `src.orchestrator` and `src.players` import cleanly.
- Docs build succeeds at **4 warnings**, none from the generated page. The
  remaining warnings are pre-existing, in `plan_multi_modality.md` pointing at a
  missing `plan.md`.
- Rendered HTML for the reference page: 160 `<strong>`, 277 `<li>`, syntax-
  highlighted JSON blocks, and **zero** leftover literal `**` or ``` — nothing
  passing through unrendered. Heading structure is `h1` page → `h2` group →
  `h3` prompt → `h4` message, with no stray headings leaking from prompt bodies.

## Scope and limits

- **The consolidation question is deliberately left open.** Extracting the seven
  `player.py` prompts into a `src/prompts/` package was considered and deferred.
  The argument for it is not de-duplication — after this change every prompt
  exists exactly once — but reviewability: a prompt change should read as a
  prompt change in a diff, not as a code change inside `execute_task`. That
  matters here because prompt tuning is the repo's central activity. It costs
  locality, so it wants its own branch. Checked and safe when done: none of the
  player prompts contain literal braces outside interpolations, so converting
  f-strings to template placeholders will not hit the brace-escaping trap.
- **Role personas should stay in `configs.py`.** A role is a cohesive unit of
  persona plus toolsets plus model settings, and `PLAYER_CONFIGS` reads well
  because adding a role is one dict entry in one file. Splitting `role_prompt`
  out would make every new role a two-file edit with a name that has to match
  across them. They are config that happens to be prose.
- **`_orphan_warning` is dormant, not dead.** It emits a warning admonition on
  the reference page listing any factory in `prompts.py` with no entry in
  `SELECTED_BY`. It currently renders nothing because there are no orphans; it
  is the guard that keeps the next one visible instead of silent.
- **`SELECTED_BY` is hand-maintained.** The "Selected by" line is the one part of
  the page not derived from source. A prompt that gains a caller without gaining
  an entry will be labelled unreferenced — conservative in the right direction
  (it over-reports orphans rather than hiding them), but it is a manual step.
