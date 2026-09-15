"""Count the chat-model calls a block of code makes.

Every model in the project is a LangChain model, and LangChain consults registered
context variables when it configures a run's callbacks. A counter placed in one for the
duration of a block is therefore attached to every model call made inside it — by the
planner, a player, the prose reader or the candidate judge — without any of them
taking a parameter for it.

The scope is a context variable rather than a global, so two runs in two threads (two
browser sessions of the demo) count separately. Work handed to a thread pool is outside
the scope unless it runs in a copy of the caller's context, as the candidate judge does.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook


class LLMCallCounter(BaseCallbackHandler):
    """Counts model requests: one per chat or completion call started."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._calls = 0

    @property
    def calls(self) -> int:
        """How many model calls have started so far."""
        return self._calls

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self._count()

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self._count()

    def _count(self) -> None:
        # Callbacks fire on whichever thread made the call.
        with self._lock:
            self._calls += 1


_active: ContextVar[LLMCallCounter | None] = ContextVar("llm_call_counter", default=None)
register_configure_hook(_active, inheritable=True)


@contextmanager
def count_llm_calls() -> Iterator[LLMCallCounter]:
    """Count the model calls made inside the block.

    Yields:
        The counter; read ``calls`` during or after the block.
    """
    counter = LLMCallCounter()
    token = _active.set(counter)
    try:
        yield counter
    finally:
        _active.reset(token)
