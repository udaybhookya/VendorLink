"""The Vendor Concierge agent: LLM with tools when a model is configured, offline intents if not."""

from __future__ import annotations

import string as _string
import time
from typing import Literal

from pydantic import BaseModel

from agents.base import Agent, AgentContext, skill
from agents.core.concierge.ports import HttpVendorPort, VendorPort
from agents.core.concierge.session import ConciergeSession
from agents.core.concierge.tools import (
    ConciergeTools,
    ConsentArgs,
    NoArgs,
    OrderArgs,
    deal_summary,
)
from agents.resources import VENDOR_PORT
from vl_core.i18n import Strings, is_affirmative, is_negative
from vl_core.llm import ChatTurn, get_tool_chat, load_prompt
from vl_core.schemas import (
    Ack,
    AgentCard,
    ChatReply,
    ConciergeChatIn,
    ConciergePhotoIn,
    UiEvent,
)


def _matches(text: str, words: list[str]) -> bool:
    lowered = text.casefold()
    return any(w.casefold() in lowered for w in words)


class Concierge(Agent):
    class Options(BaseModel):
        history_turns: int = 20
        offline_fallback: bool = True

    def __init__(self, card: AgentCard, ctx: AgentContext, options: BaseModel | None = None):
        super().__init__(card, ctx, options)
        port = ctx.resources.get(VENDOR_PORT)
        self.port: VendorPort = (
            port if port is not None else HttpVendorPort(ctx.settings.vendor_svc_url)
        )
        self.sessions: dict[str, ConciergeSession] = {}
        self.i18n_dir = ctx.settings.vl_config_dir / "i18n"

    # ---- helpers also used by the voice bridge ----------------------------------------------

    async def session(self, vendor_id: str, language: str | None = None) -> ConciergeSession:
        session = self.sessions.get(vendor_id)
        if session is None:
            vendor = await self.port.vendor(vendor_id)
            session = self.sessions[vendor_id] = ConciergeSession(vendor_id, vendor.language)
        elif not language:
            language = (await self.port.vendor(vendor_id)).language  # she may have switched
        if language:
            session.language = language
        return session

    def tools(self, session: ConciergeSession) -> ConciergeTools:
        return ConciergeTools(self.port, session, self.i18n_dir)

    async def system_prompt(self, session: ConciergeSession) -> str:
        vendor = await self.port.vendor(session.vendor_id)
        template = load_prompt(
            "concierge", session.language, self.ctx.settings.vl_config_dir / "prompts"
        )
        return _string.Template(template).safe_substitute(
            vendor_name=vendor.display_name, vendor_id=vendor.id, language=session.language
        )

    # ---- skills -------------------------------------------------------------------------------

    @skill("concierge.chat")
    async def chat(self, inp: ConciergeChatIn) -> ChatReply:
        session = await self.session(inp.vendor_id, inp.language)
        session.last_user_text = inp.text
        started = time.perf_counter()
        tools = self.tools(session)
        llm = get_tool_chat(self.ctx.settings)
        reply: str | None = None
        mode: Literal["llm", "offline"] = "offline"
        if llm is not None:
            try:
                reply = await llm.turn(
                    await self.system_prompt(session),
                    session.history,
                    inp.text,
                    tools.specs(),
                    tools.call,
                    session_key=inp.vendor_id,
                )
                mode = "llm"
            except Exception as exc:  # model busy, quota, network: degrade, never a 500
                if not self.options.offline_fallback:
                    raise
                self.ctx.log().warning(
                    "chat model failed; offline intents used",
                    extra={"fields": {"vendor_id": inp.vendor_id, "error": repr(exc)[:300]}},
                )
        if reply is None:
            reply = await self._offline(session, tools, inp.text)
        self.ctx.log().info(
            "chat turn",
            mode=mode,
            language=session.language,
            text_chars=len(inp.text),
            events=",".join(e["type"] for e in tools.events) or None,
            ms=round((time.perf_counter() - started) * 1000),
        )
        session.history += [ChatTurn("user", inp.text), ChatTurn("model", reply)]
        session.history = session.history[-self.options.history_turns :]
        return ChatReply(
            reply=reply,
            language=session.language,
            mode=mode,
            events=[UiEvent.model_validate(e) for e in tools.events],
        )

    @skill("concierge.attach_photo")
    async def attach_photo(self, inp: ConciergePhotoIn) -> Ack:
        session = await self.session(inp.vendor_id)
        session.last_photo_b64, session.last_photo_mime = inp.data_b64, inp.content_type
        return Ack()

    async def _offline(self, session: ConciergeSession, tools: ConciergeTools, text: str) -> str:
        """Deterministic intents for tests, demos without a model, and fallback."""
        s = Strings(session.language, self.i18n_dir)
        pending = session.pending
        if pending is not None and pending.kind == "question":
            result = await tools.call("answer_question", {"task_id": pending.subject_id})
            if "error" in result:
                return s.t("chat.unclear_answer", reason=result.get("detail") or result["error"])
            return str(result["say"])
        if _matches(text, s.words("intents.questions")):
            return str((await tools.call("list_questions", {}))["say"])
        if pending is not None and is_affirmative(text, s):
            result = await tools.call(
                "capture_consent",
                ConsentArgs(subject=pending.kind, subject_id=pending.subject_id).model_dump(),
            )
            if "error" in result:
                return s.t("chat.not_signed", reason=result.get("detail") or result["error"])
            return str(result["say"])
        if pending is not None and is_negative(text, s):
            return str((await tools.call("decline", NoArgs().model_dump()))["say"])
        if _matches(text, s.words("intents.deals")):
            views = await self.port.deals(session.vendor_id, session.language)
            waiting = [v for v in views if v.deal.status == "awaiting_consent"]
            if waiting:
                result = await tools.call(
                    "explain_decision", OrderArgs(order_id=waiting[0].deal.order_id).model_dump()
                )
                return str(result.get("say") or result)
            if not views:
                return s.t("chat.no_deals")
            details = " ".join(
                f"{deal_summary(v)}: {s.t(f'deal_status.{v.deal.status}')}." for v in views[:5]
            )
            return s.t("chat.deals_summary", count=len(views), details=details)
        if _matches(text, s.words("intents.rules")):
            return str((await tools.call("show_rules", {}))["say"])
        if _matches(text, s.words("intents.today")):
            return str((await tools.call("what_did_you_do_today", {}))["say"])
        return s.t("chat.not_understood")
