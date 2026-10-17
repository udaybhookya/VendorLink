"""Shared HTTP pieces for hosts that run a Liaison: the dev outbox and the background loop."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException

from vl_core.obs import get_logger
from vl_core.schemas import OutboxMessage, QuestionAnswer
from vl_core.settings import Settings

log = get_logger("hitl")


def outbox_router(channel_agent: Callable[[str], Any], settings: Settings) -> APIRouter:
    """Local channel fallback: read what would have gone to Slack / Gmail, and reply to it."""
    router = APIRouter()

    def guard() -> None:
        if not settings.vl_dev_endpoints:
            raise HTTPException(404)

    @router.get("/dev/outbox")
    async def outbox() -> list[OutboxMessage]:
        guard()
        messages: list[OutboxMessage] = []
        for name in ("slack", "gmail"):
            agent = channel_agent(name)
            if agent is not None:
                messages += await agent.outbox.query()
        return sorted(messages, key=lambda m: m.sent_at, reverse=True)

    @router.post("/dev/outbox/{channel}/{message_id}/reply")
    async def reply(channel: str, message_id: str, req: QuestionAnswer) -> OutboxMessage:
        guard()
        agent = channel_agent(channel)
        if agent is None:
            raise HTTPException(404, {"error": "unknown_channel"})
        try:
            result: OutboxMessage = await agent.add_local_reply(
                message_id, req.text, req.by or "dev"
            )
        except KeyError as exc:
            raise HTTPException(404, {"error": "unknown_message"}) from exc
        return result

    return router


def tick_lifespan(tick: Callable[[], Awaitable[Any]], settings: Settings) -> Any:
    """Run the Liaison's tick every VL_HITL_TICK_SECONDS while the service is up."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if settings.vl_hitl_tick_seconds > 0:

            async def loop() -> None:
                while True:
                    await asyncio.sleep(settings.vl_hitl_tick_seconds)
                    try:
                        result = await tick()
                    except Exception:  # keep ticking; one bad pass must not stop the loop
                        log.exception("liaison tick failed")
                        continue
                    done = {
                        k: v for k, v in result.model_dump().items() if isinstance(v, list) and v
                    }
                    if done:  # quiet when nothing happened
                        log.info(
                            "liaison tick",
                            fields={k: ",".join(map(str, v)) for k, v in done.items()},
                        )

            task = asyncio.create_task(loop())
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return lifespan
