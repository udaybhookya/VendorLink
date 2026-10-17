"""channel.slack.*: reach people on Slack through its MCP server (or the local outbox)."""

from __future__ import annotations

from agents.base import skill
from agents.channels.mcp_channel import McpChannel
from vl_core.schemas import ChannelMessage, ChannelReceipt, ChannelReplies, PollRequest


class SlackChannel(McpChannel):
    CHANNEL = "slack"

    @skill("channel.slack.send")
    async def send(self, inp: ChannelMessage) -> ChannelReceipt:
        return await self.deliver(inp)

    @skill("channel.slack.poll")
    async def poll(self, inp: PollRequest) -> ChannelReplies:
        return await self.collect(inp)
