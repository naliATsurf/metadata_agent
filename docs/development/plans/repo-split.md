# Repository Split

Move the field-driven path out of this fork into a repository of its own, then remove
the legacy half there.

Status legend: ✅ done · 🟡 partial · 🔲 not started · ⛔ blocked on a decision.

**Status: ⛔ waiting for decisions 2–5** (see the end). The name is decided.

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
- **CI would fail on the first push.** CI runs `make ci` (ruff, compileall, unittest)
  on pushes to `main`, and it has never run on this branch. Locally 409 tests pass,
  but ruff reports 2 errors, and in a fresh clone 2 tests in `tests/test_examples.py`
  fail because they need the ignored TRADAT031 bundle.
- **Nothing is lost by pushing only `provenance`.** It already contains `main`,
  `free-text`, `mlflow` and `tracking`.
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

- ✅ Commit the extraction plan (`fe29970`).
- 🟡 Back up the local-only files. `eval/data` was added to `.data-required` in
  `fe29970`; check that your backup has run since.
- 🔲 Fix the 2 lint errors: the unused `Tuple` import in `eval/labels.py:15` and the
  import below code in `tests/test_catalog.py:35`.
- 🔲 Make the two example tests pass without the sample bundle: point them at
  `data/tests/router_test`, or skip them when the bundle is missing.
- 🔲 Check the way GitHub will, in a fresh clone, then push:

  ```bash
  rm -rf /tmp/split-check
  git clone --branch provenance "$OLD" /tmp/split-check
  (cd /tmp/split-check && uv sync && make ci)
  git -C "$OLD" push origin provenance
  ```

### 2. Create the repository on GitHub 🔲

```bash
gh repo create "$OWNER/$NAME" --private \
  --description "Fills a metadata standard from a dataset's files; every value cites its source"
```

Create it with `gh repo create`, not as a fork, so it stands on its own. Start it
private (decision 3).

### 3. Push the history 🔲

```bash
cd "$OLD"
git remote add new "git@github.com:$OWNER/$NAME.git"
git push new provenance:main
git remote remove new
```

This pushes the full history, including Zehao's commits, so it still shows who wrote
what. Tags are not pushed: `v0.1.0` is the old project's release and stays behind.

### 4. Clone into a new folder 🔲

```bash
git clone "git@github.com:$OWNER/$NAME.git" "$NEW"
cd "$NEW"
uv sync
make ci
```

`make ci` should pass here before anything is copied in, since this is what GitHub
runs.

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

## Decisions for you ⛔

1. ✅ **Name.** The repository and the PyPI name are `metadata-from-sources`; the
   import package and the command are `metasource` (`metasource extract --bundle …`),
   the way `scikit-learn` imports as `sklearn`. The name says what the tool does
   ("from") and what sets it apart ("sources"). Passed over: `metadata-extraction`
   (usually means reading embedded file metadata, such as EXIF), `sourced-metadata`
   and `honest-metadata` (say nothing about extracting), `metasrc` and `mfs` (taken on
   PyPI).
2. **Owner.** Your account (`naliATsurf`) or an organization.
3. **Visibility.** *Recommendation:* private until decision 4.
4. **Data in git.** The sample bundle and the eval labels were kept out of git on
   purpose. If the data may be shared, commit at least `eval/data`, since ground-truth
   labels belong under version control. If not, keep them local and backed up.
5. **When.** *Recommendation:* before the extraction work starts, so that work happens
   in the new repository and nothing has to keep the legacy path running.
