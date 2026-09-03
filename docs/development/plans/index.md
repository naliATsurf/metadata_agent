# Plans

Design documents for work that is in progress or planned. Unlike the
[logs](../logs/index.md), these are living documents: each is updated as its
work lands, and each carries a status legend (✅ done · 🟡 partial · 🔲 not
started · ⛔ blocked on a decision) so it doubles as a progress view.

- **[Free-text extraction](free-text.md)** — extending the framework beyond
  tabular data to prose sources, by splitting the tabular assumptions out of
  `ExecutionContext` into per-modality capabilities. Ordered by component.
- **[Free-text: staged delivery](free-text-delivery.md)** — the same work
  reordered into independently shippable stages. Defers to
  [free-text.md](free-text.md) on *what* each piece is.
- **[Multi-modality reshape](multi-modality.md)** — getting the model out of the
  data path, forced by the end-to-end probe against a real deposit. Folds the
  free-text work in as its first phase.
- **[Field-driven routing](field-router.md)** — a planner that fills a metadata
  standard field by field, routing each field to the source that can answer it.
  Builds on the capability split above.

```{toctree}
:maxdepth: 1
:hidden:

free-text
free-text-delivery
multi-modality
field-router
```
