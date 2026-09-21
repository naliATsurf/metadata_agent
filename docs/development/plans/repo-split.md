# Repository Split

Move the field-driven path out of this fork into a repository of its own, then remove
the legacy half there.

Status legend: ✅ done · 🟡 partial · 🔲 not started · ⛔ blocked on a decision.

**Status: 🟡 in progress.** Every decision is made (see the end). Step 1 is done except the push to the fork; steps 2–4 talk to GitHub, and you run them.

## Why

This fork (`naliATsurf/metadata_agent`) started from `com3dian/metadata_agent` (Zehao
Lu, MIT license). The `provenance` branch is 90 commits ahead of upstream `main`: 185
files changed, +28.7k/−3.0k lines. Upstream has not changed since PR #27 was merged.

The field-driven code (`src/router`, `src/pipelines`) does not import the legacy
executor (orchestrator, players, topologies, TUI). Its only link is
`src/router/compile.py`, which uses the `Plan`/`Task` types, and
[the extraction plan](field-driven-extraction.md) removes it. The two halves share the
foundations (contexts, tools, the evidence ledger, standards, config), and those move
to the new repository.

## What the steps depend on

- **Some files exist only on this laptop.** `.data-required` lists them: `data/`,
  `logs/`, `.env`, `eval/data`. The sample bundle (TRADAT031) and the hand-written
  eval labels are among them. `CLAUDE.md`, `.claude/` and `.python-version` are also
  git-ignored and not in that list.
- **CI would have failed on the first push** (fixed in step 1). CI runs `make ci`
  (ruff, compileall, unittest) on pushes to `main`, and it had never run on this
  branch. Ruff reported 2 errors; in a clean clone, 2 tests in
  `tests/test_examples.py` needed the ignored TRADAT031 bundle, and the two app-page
  tests needed `streamlit`, which CI did not install.
- **Pushing only `provenance` carries the full history**: every commit back to Zehao's
  `initial commit` of 2026-01-06, with the same hashes, authors and dates. It already
  contains your `main`, `free-text`, `mlflow` and `tracking`. Not carried: upstream's
  `croissant` and `tui` branches (9 commits, never merged into yours; they stay in
  `com3dian/metadata_agent`), a local stash from 2026-05-01 (stashes are never
  pushed), and anything on GitHub rather than in git (issues, PR discussions,
  releases, Actions runs).
- **Nothing here changes this repository.** The steps add a new GitHub repository and
  a new folder. If something goes wrong, delete both; the fork stays as it is.

## Variables

Set these once. The commands below use them.

```bash
NAME=metadata-from-sources        # the repository name (decision 1)
OWNER=naliATsurf                  # your account or an organization (decision 2)
OLD=~/codes/metadata_agent
NEW=~/codes/$NAME
```

## Steps

### 1. Prepare the branch, in this repository

- ✅ Commit the plans (`fe29970`, `8b2db43`, `a7d4371`).
- 🟡 Back up the local-only files. `eval/data` was added to `.data-required` in
  `fe29970`; check that your backup has run since.
- ✅ Make CI pass in a clean clone (`0ecf511`): fix the 2 lint errors; skip the two
  example tests when the sample bundle is missing, and run `describe_columns` on
  `data/tests/router_test` as well; install the `demo` dependency group in CI, since
  the app-page tests need `streamlit`.
- ✅ Check the way GitHub will. In a clean copy, with `uv sync --locked
  --no-default-groups --group demo`, `make ci` passes: 410 tests, 5 skipped (the ones
  that need the ignored sample data).

  ```bash
  rm -rf /tmp/split-check
  git clone --branch provenance "$OLD" /tmp/split-check
  (cd /tmp/split-check && uv sync --locked --no-default-groups --group demo && make ci)
  ```
- 🔲 Push the branch to the fork, so the fork has the final state:
  `git -C "$OLD" push origin provenance`.

### 2. Create the repository on GitHub 🔲

```bash
gh repo create "$OWNER/$NAME" --private \
  --description "Fills a metadata standard from a dataset's files; every value cites its source"
```

Create it with `gh repo create`, not as a fork, so it stands on its own. Start it
private (decision 3).

### 3. Push the history 🔲

```bash
git -C "$OLD" push "git@github.com:$OWNER/$NAME.git" provenance:main
```

Pushing to the URL leaves this repository's remotes unchanged. To check it, `git
ls-remote "git@github.com:$OWNER/$NAME.git" main` prints the same hash as `git -C
"$OLD" rev-parse provenance`.

This pushes the full history, including Zehao's commits, so it still shows who wrote
what. Tags are not pushed: `v0.1.0` is the old project's release and stays behind.

### 4. Clone into a new folder 🔲

```bash
git clone "git@github.com:$OWNER/$NAME.git" "$NEW"
cd "$NEW"
make ci-install
make ci
```

`make ci-install` installs what CI installs, so `make ci` here is what GitHub runs,
before anything is copied in. For day-to-day work, add the groups you use, for
example `uv sync --group demo --group docs`.

### 5. Copy the local-only files 🔲

```bash
cd "$NEW"
for p in $(cat .data-required); do
  p=${p%/}
  mkdir -p "$(dirname "$p")"
  cp -R "$OLD/$p" "$(dirname "$p")/"
done
cp "$OLD/CLAUDE.md" "$OLD/.python-version" .
cp -R "$OLD/.claude" .
make ci
```

Claude Code keeps its project memory by folder path. Copy it, or the new folder starts
without it:

```bash
mkdir -p ~/.claude/projects/-Users-li000002-codes-$NAME
cp -R ~/.claude/projects/-Users-li000002-codes-metadata-agent/memory \
      ~/.claude/projects/-Users-li000002-codes-$NAME/
```

### 6. First commits in the new repository 🔲

One commit each, with `make ci` passing after each.

1. **Remove the legacy half, and the bridge.** This replaces step 1 of
   [the extraction plan](field-driven-extraction.md), which will be updated.
   - Code: `src/orchestrator/`, `src/players/`, `src/tui/`, `src/core/`,
     `src/topology.py`, `src/main.py`, `src/cli/generate.py`, and
     `src/router/compile.py` with the rest of that plan's step 1.
   - App: `demo/pages/metadata_generation.py`,
     `demo/workflows/metadata_generation.py`, the topology setting in
     `demo/settings.py`.
   - Examples: `examples/generation.py`, `mlflow_trace.py`, `wandb_weave.py`.
   - Tests: `test_orchestrator_utils.py`, `test_player_tool_dispatch.py`,
     `test_compile.py`, and the plan checks in `test_tool_registry.py` and
     `test_demo_settings.py`.
   - Docs and notebooks: `docs/_ext/promptdocs.py`, the old tutorial and architecture
     pages, `notebooks/legacy/`.
2. **Rename the package.** `src/` becomes `metasource/`. That touches 333 import lines
   in 97 files, plus `pyproject.toml`, the `makefile`, `Dockerfile`, `deploy.sh` and the
   docs. In `pyproject.toml`: `name = "metadata-from-sources"`, the console script
   `metasource = "metasource.cli:main"`, and the package include `metasource*`. The
   package installs today as a top-level `src`, which clashes with any other project
   that does the same.
3. **README and LICENSE.** A new README for the field-driven path, saying it started as
   a fork of `com3dian/metadata_agent`. `LICENSE` keeps "Copyright (c) 2026 Zehao Lu"
   and adds your line.
4. **Version 0.1.0** in `pyproject.toml`.

### 7. Afterwards 🔲

- Leave the fork as it is. Optionally, add a line to its README pointing to the new
  repository.
- Tell Zehao.
- Make the repository public once decision 4 is settled, if you want it public.

## Decisions ✅

1. ✅ **Name.** The repository and the PyPI name are `metadata-from-sources`; the
   import package and the command are `metasource` (`metasource extract --bundle …`),
   the way `scikit-learn` imports as `sklearn`. The name says what the tool does
   ("from") and what sets it apart ("sources"). Passed over: `metadata-extraction`
   (usually means reading embedded file metadata, such as EXIF), `sourced-metadata`
   and `honest-metadata` (say nothing about extracting), `metasrc` and `mfs` (taken on
   PyPI).
2. ✅ **Owner.** Your account, `naliATsurf`.
3. ✅ **Visibility.** Private. Revisit decision 4 before making it public.
4. ✅ **Data in git.** Kept local for now: the sample bundle and the eval labels are
   copied into the new folder (step 5) but not committed. Git history is permanent, so
   this is decided before anything data-related is committed. If the data may be
   shared later, commit at least `eval/data`, since ground-truth labels belong under
   version control.
5. ✅ **When.** Now, before the extraction work starts, so that work happens in the new
   repository and nothing has to keep the legacy path running.
