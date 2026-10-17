"""In-process event fan-out for Server-Sent Events (live consoles and the vendor app)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[tuple[str, str]]] = set()

    def publish(self, event: str, data: BaseModel | dict[str, Any]) -> None:
        payload = (
            data.model_dump_json() if isinstance(data, BaseModel) else json.dumps(data, default=str)
        )
        for queue in self._subscribers:
            queue.put_nowait((event, payload))

    async def subscribe(self) -> AsyncIterator[tuple[str, str]]:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


def sse_response(broadcaster: Broadcaster, only: set[str] | None = None) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        yield ": connected\n\n"
        async for event, payload in broadcaster.subscribe():
            if only is None or event in only:
                yield f"event: {event}\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"cache-control": "no-cache"}
    )
