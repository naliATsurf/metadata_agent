# Change log 2026-09-17 — Type and unit fit lowers confidence; it no longer removes candidates

**Goal:** Stop a blunt rule from deciding relevance. The veto (layer 4a) removed a
candidate whenever its type or units did not fit the field — a name field against a
numeric column, a whole-number field against fractional values — before any judge saw
it. The rules are right most of the time, but a schema's types are an author's guess
(`str` is what a schema reaches for when unsure), so a vetoed column that was in fact
right could never be recovered. It also made the catalog differ per field: the column
matcher had to partition fields by their veto set, which on `TRADAT031` split 30 fields
into calls of 16, 7, 5, 1 and 1.

## Change

- **`src/router/veto.py` → `src/router/type_fit.py`.** The rules are unchanged.
  `veto_reason` is now `mismatch(field, column)`; `apply_veto` is replaced by
  `mismatches(field, candidates, catalog)`, which lists mismatches without removing
  anything.
- **Router:** every candidate stays. A routing whose winning column does not fit caps its
  assurance at `low`, with or without a judge, and `FieldRouting.vetoed` is now
  `FieldRouting.mismatches` (the mismatches among its candidates, as `ref — reason`).
  `route_fields` and `build_plan` lose their `veto` argument.
- **Column matcher:** every field sees the same catalog, so one request covers all of
  them and the matcher splits only by size. Each card still shows the column's dtype,
  units and value range, so a mismatch is in front of the model when it decides.
- **Even splits:** `in_groups` divides fields across calls evenly — 27 fields with a cap
  of 20 become 14 + 13, not 20 + 7.
- **Eval:** `--no-veto` and the `veto` abstention attribution are gone.
- **`--debug`** prints mismatches as warnings instead of vetoes.

## Measured (`sharetrait_basic_no_trait__TRADAT031`, all readmes, 8 workers)

| | veto before matching | fit as a grade |
|---|---|---|
| matcher calls | 5 (16/7/5/1/1 fields) | **2** (15/15) |
| reader calls | 8 (20 + remainder) | 8 (11–14 each) |
| recall@5 | 11/12 | **12/12** |
| over-answered | 1/18 | 1/18 |
| accuracy | 25/30 | 25/30 |

Accuracy is unchanged: with the cards showing units and ranges, the judges did not start
picking columns the veto used to hide. The no-judge path loses the filter entirely, so
its rank 1 can now be a mismatched column; those routings are marked `low` rather than
removed.
