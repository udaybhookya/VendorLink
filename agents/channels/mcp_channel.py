"""Shared machinery for channel agents that speak MCP (not an agent itself).

With VL_CHANNELS=mcp and the server configured (MCP_<SERVER>_*), messages go to the real
workspace; otherwise they land in a local outbox where a developer can read and answer them.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from pydantic import BaseModel

from agents.base import Agent, AgentContext
from vl_core.config import ConfigLoader
from vl_core.mcp import McpClient, McpServerConfig, pick, run_operation
from vl_core.schemas import (
    AgentCard,
    ChannelConfig,
    ChannelMessage,
    ChannelReceipt,
    ChannelReplies,
    ChannelReply,
    OutboxMessage,
    PollRequest,
)


class McpChannel(Agent):
    CHANNEL: ClassVar[str]

    def __init__(self, card: AgentCard, ctx: AgentContext, options: BaseModel | None = None):
        super().__init__(card, ctx, options)
        loader = ConfigLoader(ctx.settings.vl_config_dir)
        path = loader.root / "channels" / f"{self.CHANNEL}.json"
        self.config = ChannelConfig.model_validate_json(path.read_text())
        self.client = McpClient(McpServerConfig.from_env(self.config.server))
        self.outbox = ctx.repo(OutboxMessage, "outbox")

    @property
    def live(self) -> bool:
        return self.ctx.settings.vl_channels == "mcp" and self.client.config.configured

    async def deliver(self, msg: ChannelMessage) -> ChannelReceipt:
        if not self.live:
            entry = OutboxMessage(
                id=f"out-{uuid.uuid4().hex[:10]}", message=msg, sent_at=datetime.now(UTC)
            )
            await self.outbox.put(entry.id, entry)
            if self.ctx.settings.vl_channels == "mcp":
                self.ctx.log().warning(
                    f"{msg.channel} MCP server not configured; message kept in the dev outbox",
                    message_id=entry.id,
                )
            else:
                self.ctx.log().info(
                    f"{msg.channel} message kept in the dev outbox (VL_CHANNELS=local)",
                    message_id=entry.id,
                )
            thread = msg.thread_id or entry.id
            return ChannelReceipt(
                channel=msg.channel,
                message_id=entry.id,
                thread_id=thread,
                simulated=True,
            )
        ops = self.config.operations
        op = ops["reply"] if msg.thread_id and "reply" in ops else ops["send"]
        result = await run_operation(self.client, op, msg.model_dump())
        message_id = str(pick(result, op.returns.get("message_id", "")) or "")
        thread_id = str(
            pick(result, op.returns.get("thread_id", "")) or msg.thread_id or message_id
        )
        self.ctx.log().info(
            f"{msg.channel} message sent", message_id=message_id, reply=bool(msg.thread_id)
        )
        return ChannelReceipt(channel=msg.channel, message_id=message_id, thread_id=thread_id)

    async def collect(self, req: PollRequest) -> ChannelReplies:
        if not self.live:
            entries = [
                e
                for e in await self.outbox.query()
                if e.id == req.thread_id or e.message.thread_id == req.thread_id
            ]
            # Every reply to the thread; the Liaison skips the ones it has already seen.
            return ChannelReplies(replies=[r for e in entries for r in e.replies])
        op = self.config.operations["read"]
        result = await run_operation(self.client, op, req.model_dump())
        items = pick(result, op.items or "") if op.items else result
        if isinstance(items, str):  # a server that answers in prose: treat it as one reply
            digest = hashlib.sha256(items.encode()).hexdigest()[:12]
            return ChannelReplies(replies=[ChannelReply(message_id=digest, text=items, by="")])
        replies = []
        for item in cast(list[Any], items or []):
            if any(pick(item, path) is not None for path in op.skip_if):
                continue
            message_id = str(pick(item, op.item.get("message_id", "")) or "")
            if message_id and message_id == req.thread_id:
                continue  # the question itself
            replies.append(
                ChannelReply(
                    message_id=message_id,
                    text=str(pick(item, op.item.get("text", "")) or ""),
                    by=str(pick(item, op.item.get("by", "")) or ""),
                )
            )
        return ChannelReplies(replies=replies)

    async def add_local_reply(self, message_id: str, text: str, by: str) -> OutboxMessage:
        """Local outbox only: a person's reply typed into the dev outbox (or a test)."""
        entry = await self.outbox.get(message_id)
        if entry is None:
            raise KeyError(message_id)
        reply = ChannelReply(
            message_id=f"{message_id}-r{len(entry.replies) + 1}",
            text=text,
            by=by,
            at=datetime.now(UTC),
        )
        updated = entry.model_copy(update={"replies": [*entry.replies, reply]})
        await self.outbox.put(message_id, updated)
        return updated
