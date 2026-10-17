"""Fake Slack and Gmail MCP servers for tests. Same tool names as config/channels/*.json, state in
a JSON file (FAKE_MCP_STORE) so the test can add a person's reply between calls.

    python tests/support/fake_mcp_servers.py slack|gmail
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

STORE = Path(os.environ["FAKE_MCP_STORE"])


def load() -> dict[str, Any]:
    return json.loads(STORE.read_text()) if STORE.exists() else {"slack": [], "gmail": []}


def save(data: dict[str, Any]) -> None:
    STORE.write_text(json.dumps(data))


def slack() -> MCPServer:
    app = MCPServer("fake-slack")

    @app.tool()
    def slack_post_message(channel_id: str, text: str) -> dict[str, Any]:
        data = load()
        ts = f"{time.time():.6f}"
        data["slack"].append(
            {"channel": channel_id, "ts": ts, "thread_ts": ts, "text": text, "bot_id": "B1"}
        )
        save(data)
        return {"ok": True, "channel": channel_id, "ts": ts}

    @app.tool()
    def slack_reply_to_thread(channel_id: str, thread_ts: str, text: str) -> dict[str, Any]:
        data = load()
        ts = f"{time.time():.6f}"
        data["slack"].append(
            {"channel": channel_id, "ts": ts, "thread_ts": thread_ts, "text": text, "bot_id": "B1"}
        )
        save(data)
        return {"ok": True, "ts": ts, "thread_ts": thread_ts}

    @app.tool()
    def slack_get_thread_replies(channel_id: str, thread_ts: str) -> dict[str, Any]:
        msgs = [
            m for m in load()["slack"] if m["channel"] == channel_id and m["thread_ts"] == thread_ts
        ]
        return {"ok": True, "messages": msgs}

    return app


def gmail() -> MCPServer:
    app = MCPServer("fake-gmail")

    @app.tool()
    def send_email(to: list[str], subject: str, body: str) -> dict[str, Any]:
        data = load()
        mid = f"m{len(data['gmail']) + 1}"
        data["gmail"].append(
            {
                "id": mid,
                "threadId": mid,
                "to": to,
                "from": "agent@vendorlink.example",
                "subject": subject,
                "snippet": body[:200],
            }
        )
        save(data)
        return {"id": mid, "threadId": mid}

    @app.tool()
    def search_emails(query: str, maxResults: int = 10) -> dict[str, Any]:
        token = query.split('"')[1].strip("[]") if '"' in query else ""
        hits = [
            m
            for m in load()["gmail"]
            if token in m["subject"] and m["from"] != "agent@vendorlink.example"
        ]
        return {"messages": hits[:maxResults]}

    return app


if __name__ == "__main__":
    (slack() if sys.argv[1] == "slack" else gmail()).run()
