"""Layer 4b, tools — decide which fields a tool computes.

A tool is not a source that competes with columns; it is an operation run on a table
or on columns. A row count is only an answer once you know which table to count, and
a date range once you know which column holds the dates. So the two questions are
asked apart:

1. **here**, whether a field's value is what some tool computes — asked with the
   fields and the tools only, nothing of the bundle;
2. **in the column matcher**, which table or columns the chosen tool runs on — each
   argument the tool declares (:class:`~src.tools.base.ColumnArg`) becomes one more
   line in the column matcher's field list.

Code then joins the answers (:mod:`src.router.route`): a tool whose arguments are all
bound runs; one missing an argument is dropped, and the field falls back to its column
pick or the documents.

Because this matcher never sees the bundle, its answer depends only on the schema, the
tool set and the model. With a ``cache_dir`` it is kept on disk under a hash of those,
so a standard costs a call on its first bundle and none after. A changed prompt, field,
tool or model is a different key; ``refresh`` asks again and replaces what was saved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from src import thresholds
from src.router.judge import TOOL_PREFIX, Verdict, ask, dispatch, field_lines, in_groups, pick
from src.router.schema import FieldSpec


def tool_card(tool: Any) -> Dict[str, Any]:
    """A field-answering tool as the matcher sees it: what it computes, and from what."""
    from src.tools.base import column_args_of   # lazy: importing src.tools registers every tool

    card: Dict[str, Any] = {
        "ref": f"{TOOL_PREFIX}{tool.name}", "computes": tool.description or tool.name,
    }
    needs = {name: arg.holds for name, arg in column_args_of(tool).items()}
    if needs:
        card["needs columns"] = needs
    return card


class ToolMatcher:
    """The seam: decide, for each field, the tool that computes it, or none.

    Implementations receive fields and tool cards and nothing else — no catalog, no
    context — so an answer holds for every bundle routed against the same schema.
    """

    def match(
        self, *, fields: Sequence[FieldSpec], cards: Sequence[Dict[str, Any]]
    ) -> Dict[str, Verdict]:
        raise NotImplementedError


_INSTRUCTION = (
    "You decide, for each metadata field, whether its value is computed by one of these "
    "tools from a dataset's tables.\n\n"
    "A tool answers a field only if the tool's result *is* the value the field asks for: "
    "a count of records for a field asking how many records there are, a date range for "
    "a field asking over what period data was collected. A tool that merely touches the "
    "same topic does not.\n\n"
    "You do not see the data. Some tools need columns, listed under \"needs columns\"; "
    "do not guess whether a dataset has them — that is checked afterwards. Decide only "
    "whether computing is how the field's value is obtained. A field whose value would "
    "be written down — a name, a method, a condition, a description — needs no tool.\n\n"
    "Most fields need no tool. Answering null is the normal, expected outcome.\n\n"
    'Return ONE JSON object mapping each field name to {{"choice": <the ref of the tool '
    'that computes it, or null>, "because": <why>, "confidence": "high"|"medium"|"low"}}. '
    "No prose outside the JSON, no code fence.\n\n"
    "TOOLS:\n{cards}\n\n"
    "FIELDS:\n{fields}\n"
)


class LLMToolMatcher(ToolMatcher):
    """LLM-backed tool matcher — abstention first-class.

    ``invoke`` is a callable ``prompt -> model text``. Fields are asked about in even
    groups of at most ``max_fields`` (one per call with ``batch=False``), ``max_workers``
    at a time.

    ``cache_dir`` keeps each call's parsed answer on disk; ``model`` names what
    ``invoke`` calls and is part of the key, since the prompt alone does not say which
    model answered it. ``refresh`` ignores saved answers and overwrites them with new
    ones. A failed call is not cached.
    """

    def __init__(
        self,
        invoke: Callable[[str], str],
        *,
        batch: bool = True,
        max_workers: int = 1,
        max_fields: Optional[int] = None,
        cache_dir: Optional[Path] = None,
        model: str = "",
        refresh: bool = False,
    ) -> None:
        self._invoke = invoke
        self._batch = batch
        self._max_workers = max(1, max_workers)
        self._max_fields = max_fields
        self._cache_dir = cache_dir
        self._model = model
        self._refresh = refresh
        self._cache: Dict[str, dict] = {}

    def match(
        self, *, fields: Sequence[FieldSpec], cards: Sequence[Dict[str, Any]]
    ) -> Dict[str, Verdict]:
        if not fields:
            return {}
        if not cards:
            return {f.path: Verdict(choice=None, because="no tools to match against")
                    for f in fields}
        limit = self._max_fields or thresholds.current().router_max_fields_per_call
        groups = in_groups(list(fields), limit if self._batch else 1)
        results = dispatch(lambda group: self._call(group, cards), groups, self._max_workers)
        return {path: verdict for result in results for path, verdict in result.items()}

    def _call(
        self, fields: Sequence[FieldSpec], cards: Sequence[Dict[str, Any]]
    ) -> Dict[str, Verdict]:
        prompt = _INSTRUCTION.format(
            cards=json.dumps(list(cards), indent=2, default=str), fields=field_lines(fields)
        )
        data = self._answer(prompt)
        shown = {card["ref"] for card in cards}
        return {f.path: pick(data.get(f.path), shown, "tool matcher") for f in fields}

    def _answer(self, prompt: str) -> dict:
        """The parsed answer to ``prompt``: from memory, then disk, then the model."""
        if prompt in self._cache:
            return self._cache[prompt]
        path = self._cache_path(prompt)
        if path is not None and path.is_file() and not self._refresh:
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = ask(self._invoke, prompt)
            if path is not None and data:
                path.parent.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix(".tmp")
                partial.write_text(json.dumps(data, indent=2), encoding="utf-8")
                partial.replace(path)
        self._cache[prompt] = data
        return data

    def _cache_path(self, prompt: str) -> Optional[Path]:
        if self._cache_dir is None:
            return None
        key = hashlib.sha256(f"{self._model}\n{prompt}".encode("utf-8")).hexdigest()
        return self._cache_dir / f"{key}.json"
