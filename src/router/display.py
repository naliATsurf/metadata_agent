"""Presentation for a resolved :class:`~src.router.catalog.Catalog`.

Catalog resolution (layer 3) is evidence-bearing: each column carries not just a
meaning but *how* it was resolved, on what citation, and any conflicts, corroboration,
or losing alternatives surfaced along the way. This module renders that evidence for a
terminal — a per-column overview table, a conflicts/corroboration section, and a
one-line summary — kept separate from the frozen data types so the catalog stays free
of any presentation concern.

Builders return Rich renderables (`Table`, `Text`, lists of markup) a caller can place
anywhere; :func:`render_catalog` is the convenience that prints all three to a Console
(its own, or one you pass — inject a `Console(record=True)` to capture output).
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.router.catalog import Catalog

# How each confidence grade and resolution method reads in the overview.
_CONF_STYLE = {"high": "green", "medium": "yellow", "low": "red", "none": "dim"}

# How a resolution method is named to a reader. Public so other front ends label
# a method the same way this one does.
METHOD_LABELS = {
    "structured_dictionary": "dictionary",
    "lexical_prose": "prose",
    "prose_read": "prose·read",
    "value_prior": "value",
    "none": "none",
}


def catalog_overview(catalog: Catalog) -> Table:
    """The per-column table: resolved meaning, how it was resolved, and its citation."""
    table = Table(
        title="Resolved catalog", box=box.SIMPLE_HEAVY, header_style="bold",
        title_justify="left", expand=True,
    )
    table.add_column("Table", style="dim", no_wrap=True)
    table.add_column("Column", style="cyan bold", no_wrap=True)
    table.add_column("Prior", no_wrap=True)
    table.add_column("Method", no_wrap=True)
    table.add_column("Conf", no_wrap=True)
    table.add_column("Meaning (and citation)", ratio=1)

    for c in catalog.columns:
        conf = f"[{_CONF_STYLE.get(c.link_confidence, 'white')}]{c.link_confidence}[/]"
        meaning = c.description or "[dim](unresolved — abstained)[/]"
        if c.units:
            meaning += f"  [dim]\\[{c.units}][/]"
        if c.link_evidence:
            meaning += f"  [dim]← {c.link_evidence}[/]"
        table.add_row(
            c.resource, c.name, c.value_label or "-",
            METHOD_LABELS.get(c.link_method, c.link_method), conf, meaning,
        )
    return table


def catalog_conflicts(catalog: Catalog) -> Optional[List[str]]:
    """Rich-markup lines for every column with a conflict, corroboration, or an
    alternative — the multi-source picture. ``None`` when nothing is contested."""
    interesting = [
        c for c in catalog.columns if c.conflicts or c.corroborated_by or c.alternatives
    ]
    if not interesting:
        return None
    lines = ["[bold]Conflicts, corroboration, and alternatives[/]"]
    for c in interesting:
        lines.append(f"\n[cyan bold]{c.resource}.{c.name}[/] — {c.description or '(unresolved)'}")
        for msg in c.conflicts:
            lines.append(f"  [red]✗ conflict:[/] {msg}")
        for cite in c.corroborated_by:
            lines.append(f"  [green]✓ corroborated by:[/] {cite}")
        for alt in c.alternatives:
            desc = alt.get("description") or alt.get("units") or "?"
            lines.append(
                f"  [dim]· alternative ({alt.get('method')}, {alt.get('confidence')}): "
                f"{desc} — {alt.get('evidence')}[/]"
            )
    return lines


def catalog_summary(catalog: Catalog) -> Text:
    """The one-line tally: resolved count, methods used, confidence distribution."""
    methods = Counter(c.link_method for c in catalog.columns)
    confs = Counter(c.link_confidence for c in catalog.columns)
    resolved = sum(1 for c in catalog.columns if c.link_method != "none")
    return Text.from_markup(
        f"[bold]{resolved}/{len(catalog.columns)}[/] columns resolved   "
        f"methods={dict(methods)}   confidence={dict(confs)}"
    )


def render_catalog(catalog: Catalog, console: Optional[Console] = None) -> None:
    """Print the overview, the conflicts section (if any), and the summary.

    Uses ``console`` if given (pass a ``Console(record=True)`` to capture the output),
    otherwise creates one.
    """
    console = console or Console()
    console.print(catalog_overview(catalog))
    conflicts = catalog_conflicts(catalog)
    if conflicts:
        console.print("")
        for line in conflicts:
            console.print(line)
    console.print("")
    console.print(catalog_summary(catalog))
