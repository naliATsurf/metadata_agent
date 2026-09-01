# Change log 2026-09-01 — Router UI, bundle discovery, and the routing vocabulary

**Goal:** Give the field router a page of its own, matching the catalog resolver's
([31-08-2026](31-08-2026_demo-module-pages.change.log.md)). Building it surfaced two
things worth more than the page: the two examples had **two different bundle
partitioners**, one of them still carrying a known bug, and the routing buckets were
named so poorly that reading a `FieldPlan` meant re-reading their definitions. Both
are fixed here. A third finding — the assurance grading is asymmetric in a way the
code does not justify — is recorded but **not** fixed.

## Bundle classification moved into the library

Each example had its own `discover()` deciding which files in a bundle were data,
which were codebooks, and which were documents. The resolver's had been taught to
detect a codebook structurally; the router's still split on `--dictionary` filenames
alone, so it kept the bug that put a codebook in the catalog as a data table.

`src/router/bundle.py` now owns this: `discover_bundle(root)` returns a `Bundle` of
tables / codebooks / documents, classifying by extension for documents and by shape
for codebooks (`looks_like_dictionary` — a column whose values are the bundle's
column names). Nothing has to be declared about a bundle, which is what production
wants.

Narrowing that result is a **separate** concern, `select()`, with three states:

| `--dictionary` / `--doc` | meaning |
| --- | --- |
| omitted | everything discovered — production |
| filenames | exactly those |
| `none` | none of that kind |

The third state exists because the UI must be able to express it *and* the command
line printed beside the form has to reproduce the run. An empty list cannot mean both
"all" and "none", and the two flags had silently disagreed about which — `--dictionary`
was a selection, `--doc` a filter. They are one rule now.

This is the split the demo wanted all along: the pipeline auto-discovers, the UI
overrides.

## The field router page

`examples/field_router_plan.py` moved to the `build_parser()` / `run(args, console)` /
`main()` contract, writing through an injected console instead of `print` so a run can
be captured, and returning a `RouterResult` (catalog, field plan, plan) so a caller can
render it rather than parse printed tables. It gained `--doc` (it only had
`--dictionary`), `--standard` as a `choices` list, `--no-write`, and argument groups.

`demo/components/router_view.py` renders coverage first — routed / unanswered / plan
steps, with the unanswered field paths named — then three tabs: the per-field routing
(filterable by bucket and free text), the compiled plan, and the resolved catalog via
the existing `render_catalog_view`. Reusing the catalog view whole is the payoff of
having built it as a component.

`--standard` having `choices` means it renders as a selectbox with no demo-side code,
and the argument groups became the form's layout. Both are the derive-from-the-parser
design paying off again.

`demo/components/bundle_controls.py` was extracted when the router page started
importing a private helper from the resolver page: the bundle picker, the per-kind
source pickers, and the sidebar tree now live in one component both pages import.
`catalog_resolver.py` is down to page-specific wiring.

## Catalog resolution: two bugs

**A variable spelled differently in two tables was read twice, and one spelling was
lost.** `_read_residuals` deduped residual columns on the *raw* header, so `nitrate`
and `Nitrate `, `ID` and `id`, `Mass` and `mass` were treated as different variables:
the reader was asked about **28 names for 24 variables**, and because the fold-back
also matched raw names, `EPOC.'Nitrate '` stayed unresolved while the identical
variable resolved in all five other tables. A document defines a shared variable once;
the resolver was asking as if it were several. `_match_key` already existed for exactly
this ("real CSV headers carry stray spaces") but only the dictionary path used it. Added
`_read_key` (trim + case-fold) and used it for both the dedupe and the fan-out.

**A codebook could be resolved as data.** The example partitioned CSVs by filename, so
a codebook not named by `--dictionary` became a data table and put its own headers
(`variable`, `description`, …) in the catalog. Detection is structural now, and the two
questions are properly separated: `--dictionary` decides what is *used*, and a
recognised-but-unselected codebook is **set aside**, never resolved as data. Dropping a
codebook from the list means "resolve without it", not "resolve it".

## Evidence carries its text, not just its address

`ResolvedColumn` gained `link_quote`: the text a citation points at. For a prose read
it is the span **as located in the document**, not as the model reproduced it —
`_ground_read` was already locating the span to verify it was verbatim, then discarding
the text. For a glossary hit it is the matched definition; for a codebook row, the row
rendered, which also surfaces the `notes` column that was parsed and then never read by
anything.

## One codebook for a whole bundle

`_as_dictionary` judged a codebook against **one table's** columns while
`resolve_bundle`'s docstring promised that a shared codebook "resolves the columns it
covers wherever they live". A bundle-wide codebook is mostly *other* tables' names from
any single table's point of view, so it fell under the key-column precision floor: a
24-row codebook scored 0.21–0.42 against the six tables of `TRADAT031` and every one
rejected it — **0/45 resolved**, with every column name matching.

`resolve_bundle` now passes the bundle's column vocabulary, so the same file scores
24/24. `data/sample/sharetrait_preprocessed/TRADAT031/codebook.csv` is that codebook:
one row per variable, shared identifiers stated once as the readme states them,
**45/45 columns resolved at high confidence with no conflicts**, with no flags.

The threshold was not lowered. Relaxing `_DICTIONARY_KEY_PRECISION` to 0.2 also gives
45/45 and the suite still passes, but that value is tuned to a six-table bundle and a
wider one would push it down again; the vocabulary fix is the one that generalizes.

## The routing vocabulary

The buckets were named on three different axes — `structural` classified the *fact*,
`ambiguous_structural` the *difficulty*, `narrative` the *prose style* — so none of them
stuck, and reading a routing meant recalling a definition first. They now all name the
same thing: **where the answer comes from**.

| was | now |
| --- | --- |
| `structural` | `tool` |
| `ambiguous_structural` | `column` |
| `narrative` | `document` |
| `unresolved` | `unanswered` |

Four concrete nouns, mutually exclusive, no theory to recall. Dropping "ambiguous" is
also consistent with the open decision below: the adjective was asserting an assurance
claim the code does not support.

`unresolved` → `unanswered` additionally removes a collision — the catalog has its own
"unresolved column", a different idea in the same codebase.

Two things followed for consistency, because leaving them would have kept the old
vocabulary in the artifact a reader actually looks at: the task names
(`compute_structural_fields` → `compute_tool_fields`, `extract_narrative_fields` →
`extract_document_fields`; `extract_column_fields` was already right), and the tool
candidate's `EvidenceRef.kind`, so the derivation reads
`bucket = "tool" if top.kind == "tool" else "column"`. Nothing branches on task names —
they are produced only in `compile.py` and consumed as labels. The other `kind` values
(`computed_column`, `quoted_span`) are a separate assurance vocabulary shared with the
context layer and were left alone.

The rename covers code, tests, the demo, and the two **living** design docs
(`plan_field_router.md`, `router_buckets.md`). The M1–M4 change logs keep the old
words: they record what was decided when, and rewriting them would make them lie.

## Open decision: the assurance grading is asymmetric

`_assurance` gives a `tool` routing `high` unconditionally, while a `column` routing
inherits the column's `link_confidence`. The stated justification is two-hop grounding —
a computation is recomputable, an interpretation is only as good as the catalog behind
it. **That justification does not hold**, and the code shows why: `_structured_candidates`
ranks tool descriptions and enriched columns in **one BM25 pass over one pooled corpus**.
"This field is answered by `get_item_count`" is the same lexical inference as "this field
is answered by column `la`". Choosing a tool is an interpretation too.

There are really two hops for both buckets:

1. **is this the right source for this field?** — retrieval, identical either way;
2. **is the source itself trustworthy?** — a tool recomputes; a column is as good as its
   resolution.

Today hop 2 is graded for columns and hop 1 is graded for neither. The docstring's own
principle — the weaker hop wins — is half applied.

The signal is already present and discarded: `EvidenceRef.score` carries the BM25 score
and `_structured_candidates` returns the ranked top-k, but `_assurance` reads only
`top.locator`. A field whose top two candidates are near-tied is indistinguishable from
one with a decisive winner. The M3 log already records hop 1 failing —
`record_count` routing to the `oid` column on the shared rare term "observation" — and
nothing stops it failing toward a tool, where it would be graded `high` silently.

Sketch, not implemented: grade hop 1 from the **margin** between the top candidate and
the runner-up (BM25 scores are not comparable across queries; a margin is), then
`assurance = min(hop1, hop2)`. `tool` would stop being unconditionally high. This changes
the artifact's semantics and shifts assurance values in the fixtures, so it wants its own
change and a test on the `record_count` case.

## Verified

- `python -m unittest discover -s tests` → **218 pass** after every step.
- `TRADAT031` resolves **45/45** at high confidence with no flags; `router_test` still
  **8/8** with the Kelvin conflict caught.
- The reader dedupe fix confirmed with a stub reader (no LLM): **24 names asked for 24
  variables**, down from 28, and all six `nitrate` columns resolve including EPOC's
  `'Nitrate '`.
- The single-codebook claim was tested rather than argued: a pooled 24-row codebook
  under the old code resolves **0/45**, with per-table precision printed (0.21–0.42,
  all below the 0.5 floor).
- Both demo pages run end to end headlessly; the router reports 29/33 routed, 4
  unanswered, 9 plan steps, buckets `column` / `document` / `tool` / `unanswered`.
- The two plan fixtures the example owns were **regenerated by running it**, not by
  find-and-replace.

## Scope and limits

- **Nothing tests the example contract.** `build_parser()` / `run()` are now the demo's
  API and no test imports `examples`. Breaking a signature breaks the UI silently. Still
  the cheapest insurance available, still not here.
- **Three orphaned fixtures.** `data/sample_output/field_plan.yaml`,
  `compiled_plan.yaml`, and `plan_and_fieldplan.yaml` date from August and no current
  script writes them; they keep the old bucket vocabulary. Left in place — they are not
  this change's to delete.
- **`make lint` does not check `src/`.** Ruff's `include` in `pyproject.toml` lists only
  `demo_app.py`, `demo/**`, `examples/**`, `tests/**`, confirmed with `--show-files`. The
  library itself is unlinted, which is how an unused `Searchable` import in
  `src/router/catalog.py` has gone unnoticed. Adding `src/**/*.py` would surface a
  backlog, so it is a decision, not a fix.
- **The UI writes plan artifacts by default.** `--no-write` defaults off for CLI parity,
  so every run from the router page overwrites `{standard}_field_plan.yaml` and
  `_compiled_plan.yaml`. There is a checkbox; the default may be wrong for an experiment
  bench.
- **Prose-read confidence is provenance, not truth.** `_grounding_grade` grades a read
  `high` when the located quote names the column and shares words with the description —
  but the description is written *from* that quote, so the support half is close to free,
  and units are never checked against the quote. A read claiming `units=kg` against a
  quote saying "in grams" grades `high` with no conflict. Unchanged here; noted.

## Demo fixes worth recording

Two were real bugs with non-obvious causes:

- **`set_page_config` at module level applied to one session per process.** Streamlit
  re-runs the entry script on every interaction but keeps imported modules cached, so
  configuration in `demo/app.py`'s module body ran once at first import and never again —
  the first session got `layout="wide"`, every later one silently got the default. Both
  the page config and the injected CSS moved into `main()`. Verified by counting calls:
  0 at import, one per `main()`.
- **Overriding the block container's `padding-top` hid the top control.** That padding
  clears Streamlit's fixed header; setting it smaller slid the first element underneath,
  which read as the module picker being cut in half. Left/right padding and
  `max-width: 100%` are still overridden for width; the top is not, with a comment saying
  why.

Also: the accent colour is a muted green rather than Streamlit's default red, and
multiselect chips render neutrally — a row of red tags marking the *default* selection
pointed attention at the wrong thing. Conflicts in the catalog view stopped using
`st.error` for the same reason: a conflict is the value profile catching a bad claim,
which is the system working. Red now appears only when a run actually fails.
