"""Conversational LLM with tools (the Concierge), behind an interface. Implemented on Google
ADK (`vl_core.llm.adk`); tests can override it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from vl_core.settings import Settings, get_settings


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: type[BaseModel]


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "model"]
    text: str


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolChat(Protocol):
    async def turn(
        self,
        system: str,
        history: Sequence[ChatTurn],
        user_text: str,
        tools: Sequence[ToolSpec],
        call: ToolCaller,
        session_key: str = "",
    ) -> str: ...


_override: ToolChat | None = None


def set_tool_chat_override(chat: ToolChat | None) -> None:
    global _override
    _override = chat


def get_tool_chat(settings: Settings | None = None) -> ToolChat | None:
    """None when no model is configured: callers fall back to their offline mode."""
    if _override is not None:
        return _override
    settings = settings or get_settings()
    if not settings.pro_model:
        return None
    return _adk_chat(settings.pro_model)


@cache
def _adk_chat(model: str) -> ToolChat:
    """One chat per model for the process: it holds the per-vendor sessions (the conversation)."""
    from vl_core.llm.adk import AdkToolChat

    return AdkToolChat(model)
