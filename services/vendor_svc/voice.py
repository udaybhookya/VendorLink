"""Voice channel: browser PCM over WebSocket ↔ Gemini Live, onto the same Concierge tools.

Protocol (JSON text frames from the browser, binary frames = 16 kHz mono PCM16):
  {"type": "start", "language": "te"} · binary audio · {"type": "photo", "data_b64": ...}
Server → browser: binary frames = 24 kHz PCM16 audio; JSON frames
  {"type": "transcript", "role": "user"|"model", "text": ...} and {"type": "event", ...}.
"""

from __future__ import annotations

from fastapi import WebSocket

from agents.core.concierge.agent import Concierge
from vl_core.llm.live import get_live_bridge


async def run_voice_session(ws: WebSocket, concierge: Concierge, vendor_id: str) -> None:
    await ws.accept()
    bridge = get_live_bridge(concierge.ctx.settings)
    if bridge is None:
        await ws.send_json(
            {
                "type": "error",
                "error": "voice_unavailable",
                "detail": "LIVE_MODEL is not configured; use text chat",
            }
        )
        await ws.close()
        return
    start = await ws.receive_json()
    session = await concierge.session(vendor_id, start.get("language"))
    tools = concierge.tools(session)
    system = await concierge.system_prompt(session)

    async def on_user_transcript(text: str) -> None:
        # Consent reads the vendor's own words from the input transcription, not the model.
        session.last_user_text = text

    async def flush_events() -> None:
        for event in tools.events:
            await ws.send_json({"type": "event", **event})
        tools.events.clear()

    async def call(name: str, args: dict[str, object]) -> dict[str, object]:
        result = await tools.call(name, dict(args))
        await flush_events()
        return result

    await bridge.run(
        ws,
        system=system,
        language=session.language,
        tools=tools.specs(),
        call=call,
        on_user_transcript=on_user_transcript,
    )
