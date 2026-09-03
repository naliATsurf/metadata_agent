# Development

The working record of how this project got here, in three kinds of document.
They are kept apart because they answer different questions and age differently.

- **[Plans](plans/index.md)** — forward-looking design documents. Each carries a
  status legend and is **kept current** as the work lands, so a plan is the
  source of truth for what a subsystem is meant to be and how much of it exists.
- **[Notes](notes/routing-buckets.md)** — explanatory notes on concepts that
  span several modules and are easier to read once, whole, than to reconstruct
  from the code.
- **[Logs](logs/index.md)** — dated records of what happened on a given day.
  A log is **never revised** after the fact: it is a snapshot, and a later log or
  a plan supersedes it. Two flavours: `*.change.md` describes a change that
  landed, `*.analysis.md` records findings from an investigation that changed no
  code.

New documents follow the same rules: plans in `plans/`, notes in `notes/`,
and dated logs named `YYYY-MM-DD_topic.change.md` or `.analysis.md` in `logs/`.

```{toctree}
:maxdepth: 2

plans/index
notes/routing-buckets
logs/index
```
