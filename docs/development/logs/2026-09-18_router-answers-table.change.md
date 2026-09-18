# Change log 2026-09-18 — The router page's table follows what decided the routing

**Goal:** The routing table was built for BM25 and stopped describing judged runs. Every
`Score` read 0.00, `#` ranked a set that is no longer a ranking, and the caption still
called the rows "ranked proposals, not decisions" when each one is a judge's answer.
Meanwhile what a judged run produces — the quotes and citations, the judge's reason, a
tool's bound table and columns, the mismatch and varies notes — appeared nowhere, and an
unanswered field was a blank row with no reason.

## Change

`demo/components/router_view.py` picks the table's shape from `FieldPlan.judged`, so
nothing has to be switched by hand.

**Judged — one row per field** (the new `Answers` view):

| Column | Holds |
|---|---|
| Answer from | `table::column` (+ the other tables), the tool with the table and columns it was bound to, or the citation |
| Evidence | the first quote (+ how many more), else the column's resolved meaning or what the tool computes |
| Why | the judge's own reason — including why an unanswered field was refused |
| Checks | does not fit · varies · quote not located · column and tool disagree · tool dropped |

Under the table each field is one expander holding its working: the tool matcher's choice
and its arguments, the judge's verdict, every quote with its citation (or "not located"),
the mismatch and varies notes, and the candidates. It is what `--debug` prints, read off
the same routing artifact. The expanders obey the search and bucket filters, so narrowing
the table narrows what there is to expand.

**Unjudged** keeps the ranked candidate rows with their scores: there BM25 *is* the
routing, and rank 1 is the answer unless the executor picks otherwise.

`Extractor` is dropped from the field view's columns — the compiler sets it, and the
Compiled plan tab already shows it.

`tests/test_router_view.py` covers the row builders, the checks, and that both shapes
render headlessly.
