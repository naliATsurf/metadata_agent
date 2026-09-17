"""Layer 4b, prose — read a passage for the fields it states.

Deciding whether a document answers a field is a *reading* problem. The question is
not which of several candidates holds a quantity but whether this text states a
value, and where — and the sentence that states it is the evidence. So a passage is
read once, for every field it is read for, and each answer must quote every sentence
that states the value. Locating those quotes (:func:`~src.router.catalog.locate_quote`,
in the router) is what turns "somewhere in this 20 000-character passage" into
citations a verifier can check by reading.

The referee is stricter than the column matcher's because here there is something
to check against. An answer is grounded only if **every** quote is found in the
passage — otherwise one real sentence would vouch for invented ones — and an answer
that is not grounded is kept but capped at ``low``, the grading layer 3 applies to a
prose read whose quote does not appear in its source.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src import thresholds
from src.context.base_context import EvidenceRef
from src.router.catalog import locate_quote
from src.router.judge import (
    Verdict,
    ask,
    candidate_ref,
    confidence_of,
    dispatch,
    field_lines,
    in_groups,
)
from src.router.schema import FieldSpec


def find_quote(quote: str, text: str) -> Optional[Tuple[int, int]]:
    """Where ``quote`` sits in ``text``, or ``None`` if it is not there.

    :func:`~src.router.catalog.locate_quote`, plus one allowance: a model that stops a
    sentence early tends to end it with its own full stop, and that stop is the only
    part not in the text. The span found leaves it out. Anything else not in the text —
    a changed word, two sentences joined — is still not found.
    """
    span = locate_quote(quote, text)
    trimmed = quote.rstrip().rstrip(".;,").rstrip()
    if span is None and trimmed and trimmed != quote:
        span = locate_quote(trimmed, text)
    return span


def passage_card(candidate: EvidenceRef, text: str) -> Dict[str, Any]:
    """A retrieved passage as the reader sees it: its ref, and its full text."""
    return {"ref": candidate_ref(candidate), "text": text}


#: The fields one passage is read for, and the passage.
Request = Tuple[Sequence[FieldSpec], Dict[str, Any]]


class PassageReader:
    """The seam: read one passage for the fields it states, and for none of the rest.

    :meth:`read` is one passage; :meth:`read_many` is what the router calls, and
    returns one result per request *in request order*, because the same field is
    read against several passages and the router decides between them.
    """

    def read(
        self, *, fields: Sequence[FieldSpec], passage: Dict[str, Any]
    ) -> Dict[str, Verdict]:
        raise NotImplementedError

    def read_many(self, *, requests: Sequence[Request]) -> List[Dict[str, Verdict]]:
        """Read every request, aligned with ``requests``. Default: loop :meth:`read`."""
        return [self.read(fields=fields, passage=passage) for fields, passage in requests]


_INSTRUCTION = (
    "You read a passage from a dataset's documentation and decide, for each metadata "
    "field, whether the passage states the value that field asks for.\n\n"
    "A field is stated only if the passage gives *the value the field asks for*. "
    "Mentioning a related topic is not enough, and neither is a value of a different "
    "quantity: a field wanting the temperature the animals were held at is not "
    "answered by the temperature a sample was analysed at.\n\n"
    "Most fields are not stated in any given passage. Answering stated=false is the "
    "normal, expected outcome — a wrong answer is far worse than none.\n\n"
    "Quote the sentence that states the value, copied exactly from its first word to "
    'its last — never shortened, never with "..." in it. Only if the passage states the '
    "value again in another sentence, quote that one too.\n\n"
    'Return ONE JSON object mapping each field name to {{"stated": true|false, '
    '"quotes": [<the sentence(s) that state it>] ([] when not stated), "because": <why>, '
    '"confidence": "high"|"medium"|"low"}}. No prose outside the JSON, no code '
    "fence.\n\n"
    # The passage precedes the fields so that calls over one passage share a prefix.
    'PASSAGE ({ref}):\n"""\n{text}\n"""\n\n'
    "FIELDS:\n{fields}\n"
)


class LLMPassageReader(PassageReader):
    """LLM-backed reader — abstention first-class.

    ``invoke`` is the only dependency: a callable ``prompt -> model text``. A passage
    is read for at most ``max_fields`` fields per call, or one with ``batch=False``;
    ``max_workers`` issues the calls concurrently.
    """

    def __init__(
        self,
        invoke: Callable[[str], str],
        *,
        batch: bool = True,
        max_workers: int = 1,
        max_fields: Optional[int] = None,
    ) -> None:
        self._invoke = invoke
        self._batch = batch
        self._max_workers = max(1, max_workers)
        self._max_fields = max_fields
        self._cache: Dict[str, dict] = {}

    def read(
        self, *, fields: Sequence[FieldSpec], passage: Dict[str, Any]
    ) -> Dict[str, Verdict]:
        """One call: these fields against this passage."""
        if not fields:
            return {}
        prompt = _INSTRUCTION.format(
            ref=passage["ref"], text=passage.get("text", ""), fields=field_lines(fields)
        )
        if prompt not in self._cache:
            self._cache[prompt] = ask(self._invoke, prompt)
        data = self._cache[prompt]
        return {f.path: _referee(data.get(f.path), passage) for f in fields}

    def read_many(self, *, requests: Sequence[Request]) -> List[Dict[str, Verdict]]:
        limit = self._max_fields or thresholds.current().router_max_fields_per_call
        per_call = limit if self._batch else 1
        calls = [
            (index, group, passage)
            for index, (fields, passage) in enumerate(requests)
            for group in in_groups(list(fields), per_call)
        ]
        results = dispatch(
            lambda call: self.read(fields=call[1], passage=call[2]),
            calls, self._max_workers,
        )
        merged: List[Dict[str, Verdict]] = [{} for _ in requests]
        for (index, _, _), result in zip(calls, results):
            merged[index].update(result)
        return merged


def _referee(data: Any, passage: Dict[str, Any]) -> Verdict:
    """Validate one field's answer against the passage the call showed.

    No quote is not a citation, and an answer with any quote not found in the passage
    is not grounded: either is kept but capped at ``low``. A lone ``"quote"`` string —
    the older reply shape — is read as a list of one.
    """
    if not isinstance(data, dict):
        return Verdict(choice=None, because="no usable answer from the passage reader")
    because = str(data.get("because") or "").strip()
    if str(data.get("stated")).strip().lower() not in ("true", "yes"):
        return Verdict(choice=None, because=because)
    raw = data.get("quotes", data.get("quote"))
    raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    quotes = tuple(dict.fromkeys(q for q in (str(item or "").strip() for item in raw) if q))
    text = passage.get("text", "")
    grounded = bool(quotes) and all(find_quote(quote, text) is not None for quote in quotes)
    confidence = confidence_of(data.get("confidence")) if grounded else "low"
    return Verdict(
        choice=passage["ref"], because=because, quotes=quotes,
        confidence=confidence, grounded=grounded,
    )
