# Field-Driven Extraction

The last stage of the field-driven path: turn a routed `FieldPlan` into a metadata
record in which every value cites its evidence. It replaces the compile step (M4 in
[the field-router plan](field-router.md)) and does not use the legacy executor.

Status legend: ✅ done · 🟡 partial · 🔲 not started · ⛔ blocked on a decision.

**Status: ⛔ waiting for approval.** Nothing below is built.

## Why not the executor

The compile step turns a `FieldPlan` into the `Plan`/`Task` shape so that
`PlanExecutor` can run it. That was never run end to end, and it cannot work without
rebuilding the executor:

- The executor ignores `Task.fields`, `field_bindings` and `topology`. A player sees
  only a task name such as `extract_document_fields` (`create_step_state` in
  `src/orchestrator/step_executor.py`, `Player.execute_task` in
  `src/players/player.py`).
- The executor takes one context. A bundle directory opens as tables or as text,
  never both (`src/context/context_factory.py`), so document tasks name resources the
  executor's context does not have.
- No player has a tool that reads a document.
- The resolved catalog and the router's citations never reach it. Provenance matches
  values against the tool calls made during execution, so a value taken from a
  document would come back `unverifiable`.

The router has also taken over the choosing. With judges on, each field arrives with
its source picked, and a document field arrives with the answering sentences quoted
and located. What is left per field is small and mostly deterministic.

## The path

```
resolve (3)  →  route (4)  →  extract (5)  →  record
```

`extract` takes a `FieldPlan`, in memory or from a file, and the bundle. It does not
route again: a field is read only from the sources its routing names.

## What extraction does per bucket

| Bucket | The router hands over | Extraction | Model call |
|---|---|---|---|
| tool | the tool and one argument set per run | runs the tool once per argument set | no |
| column | the matched column in every table that has it | reads the column's distinct values with `get_unique_values` (a full read; the catalog's value profile is sampled) | no |
| document | the quoted sentences and where they are | the quote reader turns the quotes into a typed value, or says the quotes do not state one | one call for all document fields |
| unanswered | nothing | nothing; the field is `not_stated` | no |

Every value is read through the tool layer, so it lands in the evidence ledger like any
tool call today. Quotes need one new text tool, `read_span(resource, start, end)`. With
it, a quote is evidence of the same kind as a tool result and can be re-read from the
file.

The quote reader is a new model role in the role registry (`src/llm_roles.py`). It
sees the field's name, description and type, and the quotes. It does not see the
document. It returns a value and the quote it came from, or "not stated". All document
fields go in one packed call, split when the call exceeds a character budget set in
`src/thresholds.py`.

## Rules

These decide what the record says when the evidence is not clean. None of them fills a
value the evidence does not give.

1. **Extraction does not choose between values.** When a single-valued field's
   evidence gives several values, the field is `varies`: the value is null and all the
   values are listed. Example: `pH` matches a column that holds two treatment levels.
2. **Disagreeing sources are not settled.** When two sources give different values,
   the field is `conflict`: null, with both values and their evidence kept.
3. **A value must fit the field's type.** It is converted with Pydantic (`"21.5"`
   becomes `21.5` for a float field). When it cannot be converted, the field is
   `invalid`: null, with the raw value kept.
4. **A required field without a value stays null and is listed as a gap.** The record
   is not validated against the standard's Pydantic model, which would reject it and
   push toward filling the gap. In TRADAT031, `doi_dataset` and `measure_date` are
   required and nothing in the bundle states them.

## Provenance and grade

Each field gets one entry:

```
FieldValue
  field, value
  status      filled | not_stated | varies | conflict | invalid
  reason      why, when not filled
  evidence    the ledger ids the value came from (tool run, column read or quote)
  citation    resource#start-end for a quote, the tool call otherwise
  match       the router's assurance: is this the right source?
  support     how the value follows from its evidence
  assurance   the weaker of match and support
```

`support` has three grades:

- **computed → high.** The value equals a tool result or a column's single value.
- **quoted → medium.** The value appears as written in the located quote.
- **interpreted → low.** The reader converted it. Example: `duration` is "in days" and
  the quote says "4 weeks", so the value 28 does not appear in the quote.

A value from a document can therefore never be graded high, and a correct-looking value
from a doubtful match stays doubtful. The support check reuses the matching in
`attribute_field` (whole result, then a leaf, then a substring), applied to the value's
own evidence only rather than to all evidence.

`match` exists today: it is the router's assurance, which combines the judge's
confidence, the catalog link's confidence and the type fit.

The record:

```
MetadataRecord
  standard
  values     {field: FieldValue}
  evidence   the ledger entries the values cite
  gaps       required fields without a value, and the varies / conflict / invalid fields
  routing    the FieldPlan it was extracted from
```

It is saved as JSON with `save` and `load`, like `ResolvedBundle`. These are new types
in the new package. `FieldProvenance` and `attribute_metadata` stay as they are, since
the legacy path uses them.

## Steps

Each step is one commit and leaves the tests passing.

1. 🔲 **Remove the bridge.** Delete `src/router/compile.py`, `tests/test_compile.py`,
   the compile half of `test_routes_fields_to_columns_across_tables` in
   `tests/test_catalog.py`, `compile_task_budget_chars`, the `fields`,
   `field_bindings` and `topology` fields on `Task`, `extractor_role` and `topology`
   on `FieldRouting`, `compile_plan` and `route_and_compile` in `src/pipelines`, the
   compiled plan in the `route` command's output and on the Field router page, and
   `data/sample_output/*compiled_plan.yaml`. Update the quickstart, the tutorials and
   `field-router.md`. After this the field-driven code no longer imports the executor
   or `src/core`.
2. 🔲 **Open the bundle for extraction, and add `read_span`.** One tabular context over
   the bundle's tables and one text context over its documents, both registered for
   tools.
3. 🔲 **Tool and column values** in a new package, `src/extract/`. No model. Rules 1
   and 3. Tested on `data/tests/router_test` with the fake judges the router tests
   already use.
4. 🔲 **Document values.** The quote reader role. Tests use a fake reader.
5. 🔲 **Grade and gaps.** The support check, the assurance, rule 2 across sources and
   rule 4.
6. 🔲 **Stage, command and page.** `src/pipelines/extraction.py`; `field_driven.run`
   returns the record; `metadata-agent extract` writes it as JSON; the Field router
   page gets a Record tab with each field's value, status, grade, citation and quote.
7. 🔲 **Eval.** Add a `value` column to `labels.csv` and score: filled right, filled
   wrong, correctly left empty, missed, and cited correctly. TRADAT031 has 26 fields,
   11 of them answerable. That is enough to catch regressions but too few to claim an
   accuracy. A second labeled bundle would help.

## Decisions for you ⛔

1. **Runs without judges.** The router's pick is then BM25's rank 1. The saved
   judge-off plan in `data/sample_output` routes `temperature` to Fulton's condition
   factor at high assurance. *Recommendation:* `extract` requires a judged routing and
   stops with a clear message otherwise.
2. **Several values, or sources that disagree.** *Recommendation:* rules 1 and 2 as
   written (null, everything kept). The alternative is to pick by assurance.
3. **Required fields without a value.** *Recommendation:* rule 4 (null, listed as a
   gap). The alternative is to fail the run.
4. **Names.** `src/extract/` for the package, `extract` for the command.

## Not in this plan

- The legacy path (`generate`, the orchestrator, players, topologies). It is left as it
  is. After step 1 the two paths share only the foundations (contexts, tools,
  standards, config) and the CLI and app entry points. Moving the field-driven path to
  its own repository is planned in [the repository split](repo-split.md).
- Settling conflicts with a model or a debate. This could come back later, for
  `conflict` fields only.
- External knowledge bases, such as Crossref, Catalogue of Life, GeoNames and
  Dataverse (Phase 2 of [the multi-modality plan](multi-modality.md)). They are
  needed for fields a bundle cannot answer, such as genus from species. The design
  above takes them without changes to what is built here:
  - A lookup is evidence like a tool call. It records the knowledge base, the query,
    the record id it returned, and the date or version. Knowledge bases change, so
    re-checking one means "same answer from that version", not "re-read the file".
  - A lookup that starts from another field (genus from species) is graded no higher
    than that field: the grade is the weaker of the lookup and its input.
  - Rule 1 applies: a name that matches several records is `varies`, never a pick.
  - The router will need fields that depend on other fields. Today every field is
    routed on its own; this is open decision 1 in [the field-router
    plan](field-router.md).
- PDF documents.
- A command that re-checks a saved record against the files. The evidence makes it
  possible, but it is not built here.
- Any change to resolution or routing.
