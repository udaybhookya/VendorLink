"""Google ADK runtime for conversational agents (the Concierge): text turns and Live voice.

The agent's tools are our own tool layer wrapped as ADK tools, so every safety rule (read-back,
consent from the vendor's own words, no signing tool) holds whatever the model decides.
ADK picks up model credentials from the environment (GOOGLE_API_KEY, or Vertex AI via
GOOGLE_GENAI_USE_VERTEXAI + GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import WebSocket
from google.adk.agents import LiveRequestQueue, LlmAgent, RunConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from vl_core.llm.toolchat import ChatTurn, ToolCaller, ToolSpec
from vl_core.llm.usage import record_usage
from vl_core.obs import get_logger

log = get_logger("adk")
APP = "vendorlink"


class SpecTool(BaseTool):
    """One of our tools, exposed to ADK. Arguments are validated by our own tool layer."""

    def __init__(self, spec: ToolSpec, call: ToolCaller) -> None:
        super().__init__(name=spec.name, description=spec.description)
        self._spec = spec
        self._call = call

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self._spec.parameters.model_json_schema(),
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        return await self._call(self.name, dict(args))


def _agent(
    name: str, model: str | BaseLlm, system: str, tools: Sequence[ToolSpec], call: ToolCaller
) -> LlmAgent:
    # An instruction *provider* is not templated by ADK, so braces in prompts stay literal.
    return LlmAgent(
        name=name,
        model=model,
        instruction=lambda _ctx: system,
        tools=[SpecTool(t, call) for t in tools],
    )


class AdkToolChat:
    """ToolChat on ADK: one ADK session per vendor keeps the conversation."""

    def __init__(self, model: str | BaseLlm) -> None:
        self._model = model
        self._sessions = InMemorySessionService()
        self._session_ids: dict[str, str] = {}

    async def turn(
        self,
        system: str,
        history: Sequence[ChatTurn],
        user_text: str,
        tools: Sequence[ToolSpec],
        call: ToolCaller,
        session_key: str = "",
    ) -> str:
        user = session_key or "anonymous"
        runner = Runner(
            app_name=APP,
            agent=_agent("concierge", self._model, system, tools, call),
            session_service=self._sessions,
        )
        session_id = self._session_ids.get(user)
        if session_id is None:
            session = await self._sessions.create_session(app_name=APP, user_id=user)
            session_id = self._session_ids[user] = session.id
        message = types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
        reply = ""
        started = time.perf_counter()
        model_calls = tokens_in = tokens_out = 0
        async for event in runner.run_async(
            user_id=user, session_id=session_id, new_message=message
        ):
            if event.author != "user" and event.content:
                model_calls += 1
            if event.usage_metadata is not None:
                n_in, n_out = record_usage(event.usage_metadata)
                tokens_in += n_in
                tokens_out += n_out
            if event.is_final_response() and event.content:
                reply = "".join(p.text or "" for p in event.content.parts or []) or reply
        log.info(
            "adk turn",
            model=_name(self._model),
            events=model_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            reply_chars=len(reply),
            ms=round((time.perf_counter() - started) * 1000),
        )
        return reply


class AdkLiveBridge:
    """Voice: browser PCM ↔ ADK run_live (Gemini Live), using the same tools."""

    def __init__(self, model: str | BaseLlm) -> None:
        self._model = model

    async def run(
        self,
        ws: WebSocket,
        *,
        system: str,
        language: str,
        tools: Sequence[ToolSpec],
        call: Callable[[str, dict[str, object]], Awaitable[dict[str, object]]],
        on_user_transcript: Callable[[str], Awaitable[None]],
    ) -> None:
        sessions = InMemorySessionService()
        runner = Runner(
            app_name=APP,
            agent=_agent("concierge_voice", self._model, system, tools, call),
            session_service=sessions,
        )
        session = await sessions.create_session(app_name=APP, user_id="voice")
        queue = LiveRequestQueue()
        run_config = RunConfig(
            response_modalities=[types.Modality.AUDIO],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async def upstream() -> None:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    queue.close()
                    return
                if message.get("bytes"):
                    queue.send_realtime(
                        types.Blob(data=message["bytes"], mime_type="audio/pcm;rate=16000")
                    )

        async def downstream() -> None:
            async for event in runner.run_live(
                user_id="voice",
                session_id=session.id,
                live_request_queue=queue,
                run_config=run_config,
            ):
                if event.usage_metadata is not None:
                    record_usage(event.usage_metadata)
                if event.input_transcription and event.input_transcription.text:
                    text = event.input_transcription.text
                    await on_user_transcript(text)  # consent reads her own words, not the model's
                    await ws.send_json({"type": "transcript", "role": "user", "text": text})
                if event.output_transcription and event.output_transcription.text:
                    await ws.send_json(
                        {
                            "type": "transcript",
                            "role": "model",
                            "text": event.output_transcription.text,
                        }
                    )
                for part in (event.content.parts or []) if event.content else []:
                    blob = part.inline_data
                    if blob and blob.data and (blob.mime_type or "").startswith("audio"):
                        await ws.send_bytes(blob.data)

        log.info("voice session started", model=_name(self._model), language=language)
        started = time.perf_counter()
        tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    log.error(
                        "voice session failed",
                        model=_name(self._model),
                        error=repr(task.exception())[:300],
                    )
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            log.info("voice session ended", seconds=round(time.perf_counter() - started))


def _name(model: str | BaseLlm) -> str:
    return model if isinstance(model, str) else model.model
