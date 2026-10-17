"""channel.gmail.*: reach people on Gmail through its MCP server (or the local outbox)."""

from __future__ import annotations

from agents.base import skill
from agents.channels.mcp_channel import McpChannel
from vl_core.schemas import ChannelMessage, ChannelReceipt, ChannelReplies, PollRequest


class GmailChannel(McpChannel):
    CHANNEL = "gmail"

    @skill("channel.gmail.send")
    async def send(self, inp: ChannelMessage) -> ChannelReceipt:
        return await self.deliver(inp)

    @skill("channel.gmail.poll")
    async def poll(self, inp: PollRequest) -> ChannelReplies:
        return await self.collect(inp)
