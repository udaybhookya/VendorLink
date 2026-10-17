"""MCP client for channel agents (Slack, Gmail, …), configured only from the environment.

    MCP_SLACK_TRANSPORT=stdio | http
    MCP_SLACK_COMMAND=npx                      MCP_SLACK_ARGS='["-y", "<slack mcp server>"]'
    MCP_SLACK_URL=https://…/mcp                MCP_SLACK_HEADERS='{"Authorization": "Bearer …"}'
    MCP_SLACK_ENV_PASSTHROUGH=SLACK_BOT_TOKEN,SLACK_TEAM_ID   # names only; values from env

Secrets never live in config files; only the names of the variables to pass through.
"""

from __future__ import annotations

import json
import os
import shlex
import string
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel, Field

from vl_core.obs import get_logger
from vl_core.schemas import McpOperation

log = get_logger("mcp")


class McpError(Exception):
    pass


class McpServerConfig(BaseModel):
    name: str
    transport: Literal["stdio", "http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    env_passthrough: list[str] = Field(default_factory=list)

    @classmethod
    def from_env(cls, name: str) -> McpServerConfig:
        prefix = f"MCP_{name.upper()}_"

        def get(key: str) -> str:
            return os.environ.get(prefix + key, "").strip()

        raw_args = get("ARGS")
        args = json.loads(raw_args) if raw_args.startswith("[") else shlex.split(raw_args)
        headers = json.loads(get("HEADERS")) if get("HEADERS") else {}
        transport = get("TRANSPORT") or ("http" if get("URL") else "stdio")
        return cls(
            name=name,
            transport=transport,
            command=get("COMMAND"),
            args=args,
            url=get("URL"),
            headers=headers,
            env_passthrough=[v.strip() for v in get("ENV_PASSTHROUGH").split(",") if v.strip()],
        )

    @property
    def configured(self) -> bool:
        return bool(self.url) if self.transport == "http" else bool(self.command)


class McpClient:
    def __init__(self, config: McpServerConfig) -> None:
        self.config = config

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        from mcp import ClientSession

        if not self.config.configured:
            raise McpError(f"MCP server {self.config.name!r} is not configured (MCP_*_ env)")
        if self.config.transport == "http":
            from mcp.client import streamable_http as transport_module

            # re-exported by the SDK's transport module (defined in a private helper module)
            create_mcp_http_client = transport_module.create_mcp_http_client  # type: ignore[attr-defined]
            streamable_http_client = transport_module.streamable_http_client

            http = create_mcp_http_client(headers=self.config.headers)
            async with streamable_http_client(self.config.url, http_client=http) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            from mcp.client.stdio import StdioServerParameters, stdio_client

            env = {k: os.environ[k] for k in self.config.env_passthrough if k in os.environ}
            env["PATH"] = os.environ.get("PATH", "")
            params = StdioServerParameters(
                command=self.config.command, args=self.config.args, env=env
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> list[dict[str, Any]]:
        async with self.session() as s:
            result = await s.list_tools()
        return [
            {"name": t.name, "description": t.description, "input": t.input_schema}
            for t in result.tools
        ]

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        """Call one tool; return structured content, else JSON-decoded text, else the text."""
        started = time.perf_counter()
        async with self.session() as s:
            result = await s.call_tool(tool, args)
        ms = round((time.perf_counter() - started) * 1000)
        if getattr(result, "is_error", False):
            log.warning(f"mcp {self.config.name} {tool} failed", ms=ms, error=_text(result)[:300])
            raise McpError(f"{tool} failed: {_text(result)[:300]}")
        log.debug(f"mcp {self.config.name} {tool}", ms=ms)
        structured = getattr(result, "structured_content", None)
        if structured:
            return (
                structured.get("result", structured) if isinstance(structured, dict) else structured
            )
        text = _text(result)
        try:
            return json.loads(text)
        except ValueError:
            return text


def _text(result: Any) -> str:
    return "\n".join(getattr(c, "text", "") for c in getattr(result, "content", []) or [])


class _Blank(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def render_args(template: Any, values: dict[str, Any]) -> Any:
    """Fill {placeholders} in an operation's args (strings, lists and dicts, recursively)."""
    if isinstance(template, str):
        return string.Formatter().vformat(template, (), _Blank(values))
    if isinstance(template, list):
        return [render_args(v, values) for v in template]
    if isinstance(template, dict):
        return {k: render_args(v, values) for k, v in template.items()}
    return template


def pick(data: Any, path: str) -> Any:
    """Dot path into nested dicts/lists ('messages.0.ts'); None when absent."""
    node = data
    for part in path.split(".") if path else []:
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


async def run_operation(client: McpClient, op: McpOperation, values: dict[str, Any]) -> Any:
    return await client.call(op.tool, render_args(op.args, values))
