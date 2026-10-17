"""channel.inapp.*: questions shown in the vendor app (and read out by the Concierge), and in the
retailer console. Answers arrive through the apps' APIs, so there is nothing to poll."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from agents.base import Agent, skill
from agents.resources import PUBLISH
from vl_core.schemas import (
    ChannelMessage,
    ChannelReceipt,
    ChannelReplies,
    OutboxMessage,
    PollRequest,
)


class InAppChannel(Agent):
    @skill("channel.inapp.send")
    async def send(self, inp: ChannelMessage) -> ChannelReceipt:
        entry = OutboxMessage(
            id=f"app-{uuid.uuid4().hex[:10]}", message=inp, sent_at=datetime.now(UTC)
        )
        await self.ctx.repo(OutboxMessage, "messages").put(entry.id, entry)
        publish = self.ctx.resources.get(PUBLISH)
        if publish is not None:
            publish(inp.to, entry)  # the host pushes it to the open app (SSE)
        return ChannelReceipt(
            channel="inapp", message_id=entry.id, thread_id=inp.thread_id or entry.id
        )

    @skill("channel.inapp.poll")
    async def poll(self, inp: PollRequest) -> ChannelReplies:
        return ChannelReplies()
