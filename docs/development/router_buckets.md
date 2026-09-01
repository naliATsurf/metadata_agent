# Reference: routing buckets and candidate `kind`s

This note explains the linked concepts at the heart of the field-driven router
(see [plan_field_router.md](plan_field_router.md)): the **candidate** the router
ranks, the **`kind`** each candidate carries, and the **bucket** assigned to every
field. It traces the full lifecycle — what produces these, who assigns them, and
everyone who reads them — so the routing decision can be reasoned about end to end.

## The three concepts, in one sentence

- **candidate** is a single `EvidenceRef`: a *location that could answer a field* —
  a proposed source, never an answer.
- **`kind`** is a *per-candidate* tag on that `EvidenceRef`: what kind of location it
  is, and therefore how trustworthy a value drawn from it would be.
- **bucket** is a *per-field* label on a `FieldRouting`: which mechanism will
  produce that field's value. A field's bucket is read off the `kind` of its
  winning candidate.

So: a field is matched to a ranked list of **candidates**; each candidate's **kind**
grades it; and the winner's kind decides the field's **bucket**. Candidate is the
unit, kind is its tag, bucket is the field-level decision derived from it.

## Candidate — the unit the router ranks

A **candidate** is one `EvidenceRef` (`src/context/base_context.py`): *a location
that could answer a field*. It is deliberately a **pointer, not an answer** — this
is the router's first linchpin, "the router proposes; the evidence disposes." A
candidate is a hypothesis about where a field's value lives; something downstream
(a computation tool, or an LLM quoting a span) later turns the location into a
value, and a verifier confirms it. Naming it a *candidate* keeps that provisional
status visible: search is honestly ranked and fuzzy and **may return nothing**.

A candidate has five fields:

| Field | Meaning |
|---|---|
| `resource` | the resource the location lives in (a table name, a document name; empty for a whole-context tool) |
| `locator` | the pointer *within* that resource — a **column name** (tabular) or a **`(start, end)` char span** (text) |
| `kind` | what sort of location it is — the assurance tag (see below) |
| `snippet` | a short human/LLM-readable preview of the location, for seeding and citation |
| `score` | the retrieval relevance (BM25) that ranked this candidate against the field query |

The router does not keep a single candidate per field — it keeps a **ranked list**
(top-*k*) on `FieldRouting.candidates`, best first. The *winner* (`candidates[0]`)
drives the field's bucket and assurance; the rest are retained so a later step (or
a human) can see the alternatives the router weighed. The compiler seeds a task's
workspace with exactly these candidates — *retrieval is the curation*, replacing a
whole-context survey with the handful of locations the field actually needs.

Where a candidate comes from names its `kind`: the field-answering **tools**, the
enriched **columns** from catalog resolution, and the document **spans** are the
three producers, each stamping the `kind` of what it returns.

## `kind` — the candidate's assurance tag

A candidate's `kind` field records what sort of location it is, and this is the
**assurance carrier**: it says how a value taken from this location would be
grounded, ordered from most to least verifiable.

| `kind` | The location is… | Value produced by | Grounding | Produced by |
|---|---|---|---|---|
| `tool` | a whole-resource **tool** (e.g. `get_item_count`) | a deterministic computation | recomputable → high | `_structured_candidates` (`route.py`), from `field_answering_tools()` |
| `computed_column` | a specific **column**, enriched with a resolved meaning | a computation over that column | recomputable, but only as sure as the column's resolution | `Catalog.search` / `TabularContext.search` |
| `verified_span` | a prose **span** a verifier has confirmed supports the value | quoting, then confirmed | medium (checked) | *reserved for the verify pass (M5); not emitted yet* |
| `quoted_span` | a prose **span**, retrieved but unconfirmed | quoting/extraction | low (retrieved only) | `TextContext.search` |

The assurance ordering is `computed_column` > `verified_span` > `quoted_span`
(with `tool` the recomputable whole-resource case). Two points worth
stressing for a report:

- **`kind` is set by the producer, not asserted by an extractor.** Each search
  method stamps the `kind` of what it returns; assurance is therefore a property of
  *where a value comes from*, never a model's self-reported confidence.
- **`verified_span` is aspirational.** It is the target state of the M5 verify pass —
  a `quoted_span` promoted once `attribute_field` confirms the produced value traces
  to it. Today the router emits only `tool`, `computed_column`, and
  `quoted_span`.

## bucket — the field's value-production mechanism

A bucket (`FieldRouting.bucket`, `src/router/route.py`) records how one schema
field will be answered. Four values:

- **`tool`** — a whole-resource fact (record count, column list), bound to a
  deterministic tool; no "which part?" question.
- **`column`** — a specific column must be identified first, then
  computed over ("which column is the temperature?").
- **`document`** — a meaning stated only in prose (abstract, licence), quoted from
  a document span.
- **`unresolved`** — nothing can answer it; flagged *before* extraction.

The bucket exists because these are three genuinely different mechanisms, each with
its own search space *and* its own assurance semantics. Collapsing them would let a
recomputable column statistic be graded like a quoted string, or ask the system to
"compute" an abstract. The bucket keeps a field's search space, value-production,
and assurance grade aligned.

## Where they live: `FieldRouting` and `FieldPlan`

Candidates, kind, and bucket are assembled into two artifact types
(`src/router/route.py`):

- **`FieldRouting`** — one per field, the complete routing record:
  `field_path` (which field), `query` (the field description used to search),
  `bucket`, `candidates` (the ranked `EvidenceRef` list), `assurance` (the grade),
  and `status` (`routed` or `unresolved`).
- **`FieldPlan`** — the persisted deliverable: `{field_path: FieldRouting}` plus a
  `coverage()` view. This is the *source of truth* the compiler turns into an
  executable plan; every concept above is stored here, not recomputed downstream.

So the containment is: a `FieldPlan` holds one `FieldRouting` per field, each
`FieldRouting` holds a bucket and a ranked list of candidates, and each candidate
holds a kind. One artifact, read top-down, is the whole routing decision.

## Creation: from `kind` to bucket

The **router** (`route_fields` → `_route_one`, `route.py`) is the sole assigner. For
each field it forms a query from the field's description, then:

1. `_structured_candidates(query, catalog)` runs **one pooled BM25 ranking** over the
   field-answering **tools** (`kind="tool"`) and the enriched **columns**
   (`kind="computed_column"`), pooled so a tool and a column compete on equal
   footing. If anything scores, the **winning candidate's `kind` names the bucket**:
   `tool` → bucket `tool`; `computed_column` → bucket
   `column`.
2. Otherwise `_search_docs` ranks the document corpus; a hit (`kind="quoted_span"`)
   → bucket `document`.
3. Otherwise → bucket `unresolved`.

```
candidate.kind          ── winning candidate ──▶     field.bucket
─────────────────                                    ────────────
"tool"     (a field-answering tool)      →     "tool"
"computed_column"(an enriched column)          →     "column"
"quoted_span"    (a document span)             →     "document"
(no candidate scored)                          →     "unresolved"
```

The two ranking passes are **separate and ordered**: their scores are not
comparable (different corpora), and structured-first encodes a preference for the
recomputable source over a quoted one whenever both exist.

### The query ranks; the search space stamps the kind

A candidate's `kind` is exactly **which search space produced it** — the mapping is
1:1, fixed before any query runs:

| Search space (producer) | `kind` |
|---|---|
| field-answering tools (structured pass) | `tool` |
| enriched columns from catalog resolution (structured pass) | `computed_column` |
| document spans (document pass) | `quoted_span` |

Two consequences worth being precise about:

- **The field-description query does not determine `kind`.** The query is *matched
  against* these spaces; it drives each candidate's `score`, which decides *which
  candidate wins*. The kind is intrinsic to the space the candidate lives in, not to
  the query. So the query selects the winner, and the winner's (space-derived) kind
  then names the bucket — the query never stamps a kind itself.
- **The two structured kinds share one ranking pass but stay distinct.** Tools and
  columns are pooled into a single BM25 ranking, yet a tool remains `tool` and
  a column remains `computed_column`: `kind` tracks the *producer*, not the pass, so
  it is finer-grained than "structured vs document."

The full chain is therefore **search space → kind → (winning candidate) → bucket**.
(The one kind outside this rule is `verified_span`: it is produced not by a search
space but by the future verify pass promoting a `quoted_span` on confirmation.)

The bucket is then stored on `FieldRouting.bucket`, inside the persisted
`FieldPlan`, and is read-only thereafter.

## A worked trace: one field, end to end

To make the chain concrete, follow a single field through `_route_one`. The bundle
is `obs.csv` (with an opaque column `la` that catalog resolution enriched, via a
codebook, to "Latitude of the survey point / decimal degrees"), a `README.md`, and
the registered tools `get_item_count` and `get_field_names`. The field is:

```python
min_latitude: Optional[float] = Field(description="the southernmost latitude sampled")
```

**Step 0 — the query.** The field's description *is* the query. It is reduced to
content terms (tokenised, stopwords dropped): `{southernmost, latitude, sampled}`.

**Step 1 — score the query against the structured space.**
`_structured_candidates` builds one little document per possible source and
BM25-scores the query terms against each:

```
from tools:    get_item_count   → "get_item_count get the number of items ... row or record count"
               get_field_names  → "get_field_names ..."
from columns:  la  → "la Latitude of the survey point decimal degrees coordinate"
               oid → "oid Observation identifier ..."
               tmp → "tmp air temperature ..."
```

Only one document contains "latitude" — the `la` column — so it is the only one
that scores. **This is where a candidate is born:** the winning source paired with
its score against *this* query.

```python
EvidenceRef(resource="obs", locator="la", kind="computed_column",
            snippet="la: Latitude of the survey point [high]", score=1.7)
```

`locator` is *where* (the column `la`); `kind` is *which space produced it* (a
column → `computed_column`); `score` is how well the query matched its text. The
query picked the winner (via score); the space fixed the kind.

**Step 2 — the winner's kind names the bucket.**

```python
top = structured[0]                 # la, kind="computed_column"
bucket = "tool" if top.kind == "tool" else "column"
```

`la`'s kind is `computed_column`, so **bucket = `column`**. (Had
`get_item_count` won, kind `tool` → bucket `tool`.)

**Step 3 — grade the assurance.** `_assurance("column", top, catalog)`
reads `catalog.get("la").link_confidence` → **`high`** (the codebook resolved `la`
with high confidence). Two-hop: the value is computable, but the *interpretation*
that `la` means latitude is only as strong as the resolution behind it.

**Step 4 — the field's routing record.**

```python
FieldRouting(field_path="min_latitude", query="the southernmost latitude sampled",
             bucket="column", candidates=[la, ...],
             assurance="high", status="routed")
```

**The same machinery, different outcomes:**

- **`license`** (query `{licence, data, released}`): no tool or column mentions
  "licence," so the structured space scores all-zero and returns empty; `_route_one`
  falls to `_search_docs`, which BM25-ranks the README's *chunks* and returns a
  `quoted_span` — kind `quoted_span` → bucket **`document`**, assurance `low`.
- **`row_count`** (query `{number, rows, observations, table}`):
  `get_item_count`'s description shares "number/row," so the **tool** wins the
  structured pass — kind `tool` → bucket **`tool`**, bound directly to
  the tool with no search over data, assurance `high`.
- **`provenance_notes`** (query `{qzzx, wvvq, blorp}`): nothing scores in either
  space → bucket **`unresolved`**, empty candidates — the coverage signal, produced
  *before* any extraction.

Two properties follow from routing being pure lexical BM25 (no embeddings):

- **The signal must be literally present in some candidate's text.** "latitude"
  reaches `la` *only* because catalog resolution injected the word "Latitude" beside
  it — the whole reason layer 3 exists. An unresolved opaque `la` scores 0 and goes
  `unresolved`.
- **It is deterministic and replayable**, which is what lets the persisted query +
  candidates be re-ranked identically by the future verifier.

## Consumption: who reads the bucket

**Within the router** (`route.py`):

- `_assurance(bucket, …)` grades the field from its bucket: `column`
  inherits the resolved column's confidence (two-hop); `document` → `low`;
  `tool` → `high`.
- `FieldPlan.coverage()` reports the `by_bucket` histogram and lists the
  `unresolved` fields — coverage known before any extraction runs.
- `FieldPlan.to_dict()` serializes the bucket for external and evaluation consumers
  of the artifact.

**The compiler** (`compile_field_plan`, `src/router/compile.py`) — the principal
consumer. It reads the bucket for five distinct decisions:

1. **Grouping** — `_group_key = (bucket, resource, tier)`; the bucket is the primary
   axis fields group along into extraction tasks.
2. **Skip-or-emit** — `unresolved` fields get *no* extraction task (named only on the
   assembly task, so the record nulls them explicitly).
3. **Player role** — `_BUCKET_PLAYER[bucket]`: `tool`/`column` →
   `data_analyst`; `document` → `metadata_specialist`.
4. **Instruction phrasing** — `_instruction(bucket, …)`: "compute via the bound tool"
   vs "extract from column *X*" vs "quote the supporting span".
5. **Target scoping** — `tool` tasks are context-level (`target_resources=[]`);
   column and span tasks target the resource that holds them.

**Downstream (future — M5/M6):** the executor runs the compiled `Plan`; the
verify/reconcile pass will lean on the bucket-implied assurance to decide replay vs
quote, and will promote a `quoted_span` to `verified_span` on confirmation. Not
built yet.

## Producer / consumer summary

| Actor | Role with respect to `kind` / bucket |
|---|---|
| tool registry (`field_answering_tools`), `resolve_catalog`, `TextContext` | **produce** the candidate spaces and stamp each candidate's `kind` |
| router (`_route_one`) | **assigns** the field's bucket from the winning candidate's `kind` |
| `_assurance`, `coverage()`, `to_dict()` | **read** the bucket to grade, report, serialize |
| compiler (`compile_field_plan`) | **reads** the bucket to group, skip, assign a player, phrase the instruction, scope the target |
| executor / verifier (future) | will **read** the implied assurance to verify, promoting `quoted_span` → `verified_span` |

## In one paragraph

The router matches each field to a ranked list of *candidates* — locations that
could answer it, proposed sources rather than answers. Every candidate carries a
*kind* — a tag for what sort of location it is (`tool` tool,
`computed_column`, `quoted_span`, or the verify-pass `verified_span`), ordered by
how verifiable a value drawn from it would be. These kinds are stamped by the producers of the candidate spaces — the tool
registry, catalog resolution, and the document sources — never by an extractor.
The router assigns each field a *bucket* — `tool`, `column`,
`document`, or `unresolved` — by reading the kind of the field's highest-ranked
candidate, and stores it on the routing artifact. The bucket is then read-only: the
router's own grader and coverage reporter consume it, and the plan compiler uses it
to group fields into tasks, decide which are answerable, choose the responsible
player, phrase the extraction instruction, and scope each task. A single routing
decision, grounded in the candidate's kind, thus propagates consistently through
grading, coverage, and execution — keeping each field's value-production mechanism,
assurance grade, and downstream handling aligned.
