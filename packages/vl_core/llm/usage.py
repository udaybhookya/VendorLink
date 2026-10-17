"""Token usage of model calls, counted per agent call.

Every model call adds its token counts to the current `usage_scope()`. The agent registry opens
one scope per agent call and reports the totals with that call's event, so the gateway can show
tokens per agent and per activity. Scopes nest without double counting: tokens spent by a
sub-agent belong to the sub-agent's own scope only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    model_calls: int = 0

    def attrs(self) -> dict[str, int]:
        """Event attributes; empty when no model was called."""
        if not self.model_calls:
            return {}
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "model_calls": self.model_calls,
        }


_current: ContextVar[Usage | None] = ContextVar("vl_llm_usage", default=None)


@contextmanager
def usage_scope() -> Iterator[Usage]:
    usage = Usage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def record_usage(metadata: Any) -> tuple[int, int]:
    """Add one model response's `usage_metadata` (google-genai / ADK) to the current scope.

    Output counts thinking tokens too, since they are billed as output. Returns (in, out).
    """
    if metadata is None:
        return 0, 0
    tokens_in = int(getattr(metadata, "prompt_token_count", None) or 0)
    tokens_out = int(getattr(metadata, "candidates_token_count", None) or 0) + int(
        getattr(metadata, "thoughts_token_count", None) or 0
    )
    usage = _current.get()
    if usage is not None:
        usage.tokens_in += tokens_in
        usage.tokens_out += tokens_out
        usage.model_calls += 1
    return tokens_in, tokens_out
