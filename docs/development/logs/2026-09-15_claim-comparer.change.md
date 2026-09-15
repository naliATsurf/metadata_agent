# Change log 2026-09-15 — Claims agree by meaning, judged by a claim comparer

**Goal:** Stop wording from deciding agreement. `_decide` treated two candidates as the
same claim only when their description and units matched after lowercasing and trimming.
An LLM rarely words a description the same way twice, so reads that agreed in meaning
("fish mass", g / "body weight of the fish", grams) were recorded as `sources disagree` at
`medium`, and corroboration almost never happened. The same held between a codebook table
and a README glossary.

## Change

- **`ClaimComparer`** (`src/router/catalog.py`, exported from `src.router`): the seam that
  groups a column's claims (`Claim`: description, units, source text) into same-meaning
  groups. The base class groups on identical wording, which is the previous behaviour.
- **`LLMClaimComparer`**: every column with differing claims in the bundle goes into one
  call. The model returns a partition into same-meaning groups — same quantity, same or
  equivalent units; related, broader or narrower is different; when unsure, apart. A
  failed call, or an answer that is not a partition of a column's claims, falls back to
  identical wording for that column.
- **`resolve_catalog` / `resolve_bundle`** take `claim_comparer=`. In
  `examples/resolve_catalog.py`, `--llm-reader` builds both the reader and the comparer on
  the same model, so the demo page gets it with the LLM reader checkbox.
- **`_decide`** uses the groups: a candidate in the chosen claim's group corroborates, a
  pool spanning several groups is contested. A read quoting the same sentence as the chosen
  read does not corroborate (a README copied into a longer document states a meaning once);
  codebook entries are exempt, since a row or glossary entry is the claim itself.

## Restructure

Resolution was decide-then-re-decide: Phase 1 decided every column, and the reader pass
re-decided residuals. Comparing claims needs all candidates first, so each column is now
decided once:

| Before | After |
| --- | --- |
| `_resolve_table_deterministic` → decided columns | `_gather_table` → `_TableEvidence` of `_ColumnEvidence` (candidates, no decision) |
| `_resolve_column` | `_deterministic_candidates` |
| residual = `link_method == "none"` | residual = no candidates (the same columns) |
| read candidates as dicts, grounding conflict keyed by evidence | `_Candidate` with a `conflict` field, built by `_read_candidate` |
| `_decide(name, dtype, resource, label, profile, candidates, ground_conflicts)` | `_decide(resource, column)` after `_compare_claims` |

`alternatives` entries gain a `conflict` key (`null` unless the losing read's evidence was
unconfirmed).

Without a comparer, results are unchanged except for the copied-sentence rule.

Tests: seven in `ProseReaderTierTest` — paraphrases corroborate or disagree by the
comparer's grouping, no call when claims are identical, copied sentences, one call for many
columns, and the fallback on bad answers and failed calls.
