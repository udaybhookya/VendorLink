"""The Concierge on Google ADK, driven by a scripted model (no network). Proves the ADK wiring
and that the model cannot consent on the vendor's behalf: consent uses her own words."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

from scripts._demo import MAYA, call, day, deal, place, reset, set_language
from vl_core.llm import set_tool_chat_override
from vl_core.llm.adk import AdkToolChat
from vl_core.settings import Settings


class ScriptedModel(BaseLlm):
    """Plays the model: per user message, call these tools in turn, then say this text."""

    model: str = "scripted"
    script: dict[str, Any] = Field(default_factory=dict)
    seen_tools: list[str] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        contents = llm_request.contents
        # The latest message the person typed, and how many tool results came back since.
        last_user = max(
            i
            for i, c in enumerate(contents)
            if c.role == "user" and any(p.text for p in c.parts or [])
        )
        user_text = next(p.text for p in contents[last_user].parts or [] if p.text)
        done = sum(1 for c in contents[last_user:] for p in c.parts or [] if p.function_response)
        steps = self.script[user_text]
        if done < len(steps["tools"]):
            name, args = steps["tools"][done]
            self.seen_tools.append(name)
            part = types.Part.from_function_call(name=name, args=args)
        else:
            last = (
                next(
                    (
                        p.function_response.response
                        for c in reversed(contents)
                        for p in c.parts or []
                        if p.function_response
                    ),
                    None,
                )
                or {}
            )
            text = steps["say"].format(**{k: v for k, v in last.items() if isinstance(v, str)})
            part = types.Part.from_text(text=text)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    m = ScriptedModel()
    set_tool_chat_override(AdkToolChat(m))
    yield m
    set_tool_chat_override(None)


async def test_adk_concierge_reads_back_and_takes_her_yes(
    stack: Settings, model: ScriptedModel
) -> None:
    await reset(stack, "A")
    await set_language(stack, "te")  # she switches the app to Telugu
    order = await place(stack, "store:hub-9", day(3))
    oid = order["order_id"]
    model.script = {
        "deals?": {
            "tools": [("list_deals", {}), ("explain_decision", {"order_id": oid})],
            "say": "{say}",
        },
        "అవును": {
            "tools": [("capture_consent", {"subject": "deal", "subject_id": oid})],
            "say": "{say}",
        },
    }
    chat = f"/vendors/{MAYA}/chat"
    first = await call(stack.vendor_svc_url, "POST", chat, json={"text": "deals?"})
    assert first["mode"] == "llm" and "280" in first["reply"]
    assert model.seen_tools == ["list_deals", "explain_decision"]
    second = await call(stack.vendor_svc_url, "POST", chat, json={"text": "అవును"})
    assert (await deal(stack, oid))["deal"]["status"] == "signed", second


async def test_the_model_cannot_consent_for_her(stack: Settings, model: ScriptedModel) -> None:
    await reset(stack, "A")
    oid = (await place(stack, "store:hub-9", day(3)))["order_id"]
    model.script = {
        "deals?": {"tools": [("explain_decision", {"order_id": oid})], "say": "{say}"},
        # She says no, but a misbehaving model tries to record consent anyway.
        "వద్దు": {
            "tools": [("capture_consent", {"subject": "deal", "subject_id": oid})],
            "say": "done",
        },
    }
    chat = f"/vendors/{MAYA}/chat"
    await call(stack.vendor_svc_url, "POST", chat, json={"text": "deals?"})
    await call(stack.vendor_svc_url, "POST", chat, json={"text": "వద్దు"})
    assert (await deal(stack, oid))["deal"]["status"] == "awaiting_consent"  # nothing signed


class BusyModel(BaseLlm):
    """A model that is down (503 / quota): every call raises."""

    model: str = "busy"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise RuntimeError("503 UNAVAILABLE: model is experiencing high demand")
        yield  # pragma: no cover - makes this an async generator


async def test_model_outage_falls_back_to_offline_intents(stack: Settings) -> None:
    await reset(stack, "A")
    set_tool_chat_override(AdkToolChat(BusyModel()))
    try:
        reply = await call(
            stack.vendor_svc_url, "POST", f"/vendors/{MAYA}/chat", json={"text": "deals"}
        )
    finally:
        set_tool_chat_override(None)
    assert reply["mode"] == "offline" and reply["reply"]
