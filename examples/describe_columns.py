"""Example: catalog resolution — what it produces, and what that buys.

A column header is not a meaning. ``ucrit``, ``p50`` and ``la`` say nothing to a metadata
schema, and no amount of searching reaches them. Catalog resolution (layer 3) fixes that
before any routing happens: it harvests each column's meaning from the bundle's *other*
files — a codebook table, a glossary in a README, narrative prose — cross-checks every
borrowed claim against the column's own values, and records the citation it came from.

This runs the stage on its own (:mod:`src.pipelines.catalog`) and prints what comes back:

1. every resolved column — meaning, units, how it was resolved, how sure, and the citation;
2. the evidence behind one: the sentence it was read from, and any source that agreed;
3. the payoff — searching the *enriched* catalog in schema words reaches ``ucrit``;
4. the same bundle without its codebook, resolved from the README's glossary instead.

Deterministic: no model, no credentials. Pass ``readers=build_readers()`` to have a model
read the narrative that no codebook covers::

    python examples/describe_columns.py
    python examples/describe_columns.py data/tests/router_test
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

# Make the repo importable when run directly (python examples/describe_columns.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pipelines.catalog import DEFAULT_BUNDLE, resolve_directory

QUERIES = ("critical swimming speed", "acclimation pH treatment", "oxygen uptake at rest")


def main(bundle: Path = DEFAULT_BUNDLE) -> None:
    resolved = resolve_directory(bundle)
    catalog = resolved.catalog
    sources = [p.name for p in (*resolved.codebooks, *resolved.documents)]
    print(f"{bundle.name}: {len(catalog.columns)} columns, resolved from {sources}\n")

    print(f"{'table::column':<38}{'meaning':<44}{'units':<10}{'how':<24}confidence")
    for column in catalog.columns:
        print(f"{column.resource + '::' + column.name:<38}"
              f"{(column.description or '—')[:42]:<44}"
              f"{(column.units or '—')[:8]:<10}"
              f"{column.link_method:<24}{column.link_confidence}")

    print("\nHow they were resolved:", dict(Counter(c.link_method for c in catalog.columns)))

    # Every resolution carries its evidence: a citation into the file it came from, and
    # whoever else said the same thing. That is what a later verifier re-checks.
    cited = [c for c in catalog.columns if c.link_evidence]
    print("\nEvidence — each meaning points back at where it was found:")
    for column in cited[:3]:
        print(f"  {column.resource}::{column.name} ← {column.link_evidence}"
              + (f"\n      quoted: {column.link_quote!r}" if column.link_quote else "")
              + (f"\n      agreed with by: {column.corroborated_by}" if column.corroborated_by else "")
              + (f"\n      other claims considered: {len(column.alternatives)}"
                 if column.alternatives else ""))

    # The payoff. The router searches the enriched catalog, not the headers, so a query
    # written in the schema's words reaches a column whose name shares none of them.
    print("\nSearching the enriched catalog (the router's own search):")
    for query in QUERIES:
        hits = catalog.search(query, k=3)
        found = ", ".join(f"{h.resource}::{h.locator} ({h.score:.1f})" for h in hits) or "nothing"
        print(f"  {query!r} → {found}")
    print("  The headers alone would answer none of these.")

    # Take the codebook away and the same meanings come from the README's glossary — a
    # lower tier, so a lower base confidence, and one column nothing describes at all.
    bare = resolve_directory(bundle, dictionaries=["none"])
    print("\nWithout the codebook:",
          dict(Counter(c.link_method for c in bare.catalog.columns)))
    for column in bare.catalog.columns:
        if column.link_method not in ("none", "structured_dictionary"):
            print(f"  {column.resource}::{column.name} — {column.description} "
                  f"[{column.link_method}, {column.link_confidence}] {column.link_evidence}")
            break
    unresolved = sorted({c.name for c in bare.catalog.columns if c.link_method == "none"})
    print(f"  nothing describes: {', '.join(unresolved) or 'every column is described'}")
    print("  those are the columns a prose reader would be asked about, and only those:")
    print("  resolve_directory(bundle, readers=build_readers())")

    conflicts = catalog.conflicts
    print(f"\n{len(conflicts)} conflict(s) recorded"
          + ("".join(f"\n  {line}" for line in conflicts[:5]) if conflicts else ""))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BUNDLE)
