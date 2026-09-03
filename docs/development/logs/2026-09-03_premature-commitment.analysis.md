# Analysis log 2026-09-03 — The FieldPlan defers a choice its consumers make anyway

**Goal:** Establish whether the field router's central claim — *"the router
proposes a set; the evidence disposes"* — actually holds. It does not. Three
consumers commit to the top-ranked candidate at build time, while the artifact
presents the choice as open. No code changed; findings only.

**Method:** Read `_route_one` and `_assurance` (`src/router/route.py`) and
`_group_key` / `_extraction_task` (`src/router/compile.py`). Then routed both
fixtures — `TRADAT031` against `sharetrait_basic`, `data/tests/router_test`
against `field_router_test` — at `k=5` and measured how often rank 1 is decisive.

**Prompted by** reading the router page's candidate table, which renders one row
per ranked candidate. Displaying the artifact as it actually is made the gap
between "a ranked set" and "a decided mapping" visible.

## The claim

`compile.py` states it plainly:

> **Proposes a set per field; it does not commit.** … The executor chooses which
> candidate actually answers each field: the router proposes, the evidence
> disposes. An answer the lexical router ranked second (a right span it
> under-scored) stays reachable, so correctness depends on the router's
> **recall**, not its precision@1.

`_field_bindings` carries every candidate onto the task, so at the level of the
serialized artifact this is true. The problem is what three other pieces of code
do with rank 1 on the way there.

## Three commitments to rank 1

**1. The bucket.** `_route_one` reads the mechanism off the winning candidate:

```python
top = structured[0]
bucket = "tool" if top.kind == "tool" else "column"
```

A bucket is a statement about *which mechanism will produce the value*, and that
is a property of the candidate eventually chosen — not of the set. Assigning it
before anything is chosen is the choice, made early and recorded as if it were
structure.

It is not decorative. The compiler reads the bucket for four decisions:
`_BUCKET_PLAYER[bucket]` (who extracts), `_TASK_NAME[bucket]` (a tool computation
or a column extraction), `_topology_for` (single or debate), and — via
`target = [] if bucket == "tool" else [resource]` — **what the task opens**.

**2. The task's resource.** `_group_key` takes the resource from rank 1 alone:

```python
resource = routing.candidates[0].resource if routing.candidates else ""
```

That resource becomes the task's `target_resources`. So the *column* choice stays
open while the *table* is fixed. A lower-ranked candidate in another table is
carried in `field_bindings` and cannot be reached, because the task never opens
the table it lives in.

**3. The assurance.** `_assurance` grades from `top` and ignores the rest,
including `EvidenceRef.score`, which the router computes and keeps. This is the
asymmetry recorded in the
[2026-09-01 log](2026-09-01_router-ui-and-vocabulary.change.md); it belongs to the
same root cause and is not restated here.

The pattern: **the artifact defers, and every consumer commits.** Recall is
preserved in the serialized form and spent before execution.

## How often rank 1 is arbitrary

| | TRADAT031 / sharetrait_basic | router_test / field_router_test |
|---|---|---|
| routed fields | 29 | 11 |
| rank 1 tied with rank 2 on score | **6** | 1 |
| candidate set spans more than one `kind` | 1 | 1 |
| candidate set spans more than one resource | **12** | 0 |

Three things follow.

**A tie is decided by iteration order.** Six of 29 fields on the real bundle have
rank 1 and rank 2 scoring identically, so "first" means whichever the corpus was
built in. `genus_name` is the clearest: five candidates, every one the column
`pH`, in five different tables, all at **2.04**. The router has no basis to prefer
any of them; `Blood nitrate_nitrite` wins because its table was globbed first. The
compiler then scopes the task to that table, and the other four become unreachable.

**Cross-table alternatives are the normal case, not an edge case.** 12 of 29
fields have candidates in more than one resource. That is expected here — `pH`,
`nitrate`, `tank`, `ID`, and `mass` each occur in five or six of the six tables —
but it means the rank-1 resource commitment applies to 41% of routed fields, and
each time it discards the rest of the set.

**Mixed-mechanism sets are rare but real, and this is where it bites hardest.**
One field in each fixture has candidates of more than one `kind`:

```
TRADAT031    duration_generation   bucket=column   [('computed_column', 5.29), ('tool', 1.69)]
router_test  record_count          bucket=column   [('computed_column', 2.00), …, ('tool', 0.85)]
```

`record_count` is the case the
[M3 log](2026-07-22_field-router-m3.change.md) already flags: it should be answered
by `get_item_count`, which sits at rank 4, while BM25 puts the `oid` column first
on the shared rare term "observation". The bucket therefore becomes `column`, the
field compiles into `extract_column_fields` scoped to one table, and the tool that
answers it is never called. The set contains the right answer; the execution shape
excludes it.

M3 recorded this as a lexical-ranking limitation. It is that, but it is also a
**structural** one: even a perfect ranker leaves near-ties, and the design says
those should be resolved by the executor. Here they are resolved by `[0]`.

## Where this leaves the design

The narrow reading is that `bucket` is misplaced: it is an execution-time property
sitting on a routing-time artifact. That reading is right but incomplete, because
the bucket is load-bearing — removing it leaves four decisions in `compile.py`
without a basis.

The question is really: **what should a FieldPlan commit to, and what should it
defer?** Three coherent answers, none obviously best:

- **Commit honestly.** Keep the bucket and rank-1 resource, and drop the claim that
  the executor chooses. The artifact becomes a mapping, `field_bindings` becomes
  advisory, and precision@1 becomes the metric that matters. Simplest, and gives up
  the inversion the router exists for.
- **Defer honestly.** Remove `bucket` from `FieldRouting`; let a task span
  mechanisms and resources so the executor can reach any candidate in the set. The
  compiler needs a new grouping basis, `coverage()` becomes a distribution over
  proposals rather than decisions, and a task must be able to both call a tool and
  open a table. Most faithful to the stated design, most work.
- **Commit where it is safe, defer where it is not.** Commit when rank 1 wins by a
  margin; keep the set open when the top candidates are tied or span mechanisms.
  This needs the margin signal `_assurance` is already discarding, and it fits the
  proposal in the 2026-09-01 log to grade hop 1 from the retrieval itself. It also
  means a task's shape becomes conditional, which is new complexity in the compiler.

## What is not in question

- `field_bindings` genuinely carries the full ranked set; the serialization is fine.
- Coverage — a field nothing answers is flagged before extraction — is unaffected.
  `unanswered` is a real absence of candidates, not a pick.
- The 2026-09-01 rename (`tool` / `column` / `document` / `unanswered`) is
  orthogonal and stands either way. If buckets move to execution time the same four
  names apply there.

## Recommendation

Treat this as an M5 design question alongside the verify pass, not as a patch.
The verify pass has to decide what a produced value is checked *against*, which is
the same question from the other end: if the executor may pick any candidate, the
verifier must confirm the value traces to the candidate actually used, not to
rank 1. Deciding "what does a FieldPlan commit to" first would give the verifier
its contract.

Until then the three commitments should at least be **visible**. The router page's
candidate view shows the ranked set and per-candidate `kind`, so a tie or a
cross-table alternative can be seen. Nothing yet shows that the compiler discarded
them.
