# Change log 2026-09-14 — A glossary is accepted on its structure, not its separators

**Goal:** Stop the catalog resolver from reading equations and stray sentences as column
definitions. The glossary regex (`lexical_prose`) took any `term <sep> text` as a
definition. In a PDF-extracted Methods paragraph, `AAS=Inline graphicO2MAX Inline
graphicO2STANDARD) and factorial aerobic scope` resolved `aas` to "Inline graphicO2MAX
Inline graphicO2STANDARD) and factorial a", cut off at the regex's 61-character cap.

Nothing downstream could correct it. The column now had a candidate, so it was no longer
residual and the LLM reader never saw it. The value profile can refute units and ranges,
not a garbage description.

## What was wrong

`:`, `=` or a spaced dash says nothing on its own: `la = latitude` and `AAS = MO2max −
MO2standard` share the `=`. What makes a README's `pH – acclimation pH; nitrate – nominal
nitrate treatment concentration (mg/L); tank – replicate tank ID; …` a codebook is its
structure: many adjacent entries whose terms are the dataset's own column names. That is
already the evidence `_as_dictionary` accepts a codebook *table* on.

## The change

- **`lexical_prose` is replaced by `text_codebook`.** `_as_text_codebook` parses every
  `term <sep> definition` entry in a document and groups adjacent well-formed entries into
  runs. It accepts a run only if it has at least `_TEXT_CODEBOOK_MIN_ENTRIES` (3) entries
  and `_DICTIONARY_KEY_PRECISION` of its terms are schema names. An entry must be shaped
  like a definition (`_is_definition`: balanced brackets, no arithmetic, no stray spaced
  dash, no dangling function word), and a malformed entry ends its run. A definition stops
  at `;`, a paragraph break, a line into a list item or heading, a sentence end, or the
  next entry's head — so `Mass – fish mass (g) Duration - recovery duration (min)` splits
  without the old `_trim_absorbed_definition` patch. Trailing units, including a doubled
  `((mg O2 kg-1 h-1))`, are split off.
- **Isolated matches are dropped.** They are narrative, and go to the reader.
- **An accepted run is a `_Dictionary`.** `_Dictionary` now carries its `method` and base
  `confidence`, and entries are typed (`_Entry`: key, description, units, evidence, quote)
  instead of a dict. Text entries cite a `resource#start-end` span, like a prose read.
- **Lower assurance than a table.** `text_codebook` has rank 2 and base confidence
  `medium`; `structured_dictionary` stays at rank 3 and `high`. A table wins a
  disagreement, and agreeing text is recorded as corroboration.
- **`DeterministicProseReader` is removed.** It ran the same forward regex over localized
  chunks, so it added nothing beyond the old tier. With it go `--prose-reader` in both
  examples and the demo's "deterministic" prose tier. The prose tiers are now `off` and
  `llm`.

## Effect

On the sample README (`data/sample/Readme.txt`), all 23 real entries parse, with units
(the tautological `p50 – p50` is skipped). Across every bundle in `data/` that has
documents, the only resolutions that changed were two `lexical_prose` false positives from
`end2end_test/record/CAVEATS.md` (`comment_trait` → "lossy and unqueryable",
`photoperiod` → "wrong"). Both now abstain.

Known gap: a glossary written as a markdown table (`| la | latitude |`) is not parsed.
