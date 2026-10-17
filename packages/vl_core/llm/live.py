"""Voice channel interface. Implemented on Google ADK `run_live` (Gemini Live) in
`vl_core.llm.adk`; text chat is the fallback. Not exercised against the real model in CI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from fastapi import WebSocket

from vl_core.llm.toolchat import ToolSpec
from vl_core.obs import get_logger
from vl_core.settings import Settings, get_settings

log = get_logger("live")

LiveToolCaller = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class LiveBridge(Protocol):
    async def run(
        self,
        ws: WebSocket,
        *,
        system: str,
        language: str,
        tools: Sequence[ToolSpec],
        call: LiveToolCaller,
        on_user_transcript: Callable[[str], Awaitable[None]],
    ) -> None: ...


def get_live_bridge(settings: Settings | None = None) -> LiveBridge | None:
    settings = settings or get_settings()
    if not settings.live_model:
        return None
    from vl_core.llm.adk import AdkLiveBridge

    return AdkLiveBridge(settings.live_model)
