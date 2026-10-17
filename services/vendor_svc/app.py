from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Header, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import Request

from agents.core.concierge.agent import Concierge
from agents.registry import AgentBlocked
from agents.resources import VENDOR_PORT
from vendor_svc.concierge_port import InProcessVendorPort
from vendor_svc.operations import VendorOperations
from vendor_svc.seed import seed_vendors
from vendor_svc.store import PREFIX
from vl_core.a2a import GatewayError, GatewayVerifier, a2a_router
from vl_core.crypto import Signer, get_signer
from vl_core.devguard import require_demo
from vl_core.errors import Problem, error_response
from vl_core.hitl_http import outbox_router, tick_lifespan
from vl_core.i18n import Strings
from vl_core.obs import log_startup
from vl_core.obs.tracing import TracingMiddleware
from vl_core.registry import RegistryError
from vl_core.schemas import (
    Ack,
    ActivityItem,
    ChatReply,
    ChatRequest,
    ConciergeChatIn,
    ConciergePhotoIn,
    ConsentRequest,
    DealView,
    DirectoryEntry,
    DocumentUpload,
    HumanTask,
    Listing,
    ListingConfirmRequest,
    ListingDraftView,
    ListingSubmission,
    ProfileUpdate,
    QuestionAnswer,
    RequiredDocs,
    RulesDraft,
    RulesView,
    StandingMandate,
    TickIn,
    TickOut,
    Vendor,
    VendorCheckOut,
    VendorDoc,
    VendorInvite,
    VendorOptions,
    VendorRegistration,
    VendorTerms,
)
from vl_core.settings import Settings, get_settings
from vl_core.store import reset_repos

SERVICE = "vendor-svc"


def create_app(settings: Settings | None = None, signer: Signer | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_startup(SERVICE, settings)
    services = VendorOperations(settings, signer or get_signer("vendor", settings))
    # The Concierge acts on vendor state only through this port: no signer, no store access.
    services.agents.resources[VENDOR_PORT] = InProcessVendorPort(services)

    async def tick() -> TickOut:
        return await services.agents.call("human.tick", TickIn(), TickOut)

    def channel(name: str) -> Any:
        r = next((r for r in services.agents.registered() if r.card.agent_id == name), None)
        return services.agents.agent(name) if r is not None and r.route == "local" else None

    app = FastAPI(title=SERVICE, lifespan=tick_lifespan(tick, settings))
    app.state.operations = services
    app.include_router(outbox_router(channel, settings))
    app.add_middleware(TracingMiddleware, service=SERVICE)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def concierge() -> Concierge:
        agent = services.agents.agent("concierge")
        assert isinstance(agent, Concierge)
        return agent

    @app.exception_handler(Problem)
    async def problem(_: Request, exc: Problem) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.detail)

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        status = exc.status if 400 <= exc.status < 500 else 502
        return error_response(status, exc.code, exc.detail)

    @app.exception_handler(AgentBlocked)
    async def agent_blocked(_: Request, exc: AgentBlocked) -> JSONResponse:
        return error_response(423, "agent_blocked", str(exc))

    @app.exception_handler(RegistryError)
    async def registry_error(_: Request, exc: RegistryError) -> JSONResponse:
        return error_response(503, "agent_unavailable", str(exc))

    app.include_router(
        a2a_router(
            GatewayVerifier(settings.trust_gateway_url),
            {
                "order.propose": services.on_order_propose,
                "order.counter_response": services.on_counter_response,
                "terms.propose": services.on_terms,
                "vendor.status": services.on_vendor_status,
                "listing.status": services.on_listing_status,
                "vendor.invite": services.on_invite,
                "info.request": services.hitl.on_info_request,
            },
            services.owns_agent,
        )
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"service": SERVICE, "store": settings.vl_store, "signer": settings.vl_signer}

    @app.get("/vendors")
    async def vendors() -> list[Vendor]:
        return await services.store.vendors.query()

    @app.post("/vendors")
    async def register(req: VendorRegistration) -> Vendor:
        """Supplier self-registration: creates the vendor and her agent at the gateway."""
        return await services.register(req)

    @app.get("/options")
    async def options() -> VendorOptions:
        return await services.options()

    @app.get("/directory/retailers")
    async def retailers() -> list[DirectoryEntry]:
        return await services.directory("retailer")

    @app.get("/vendors/{vendor_id}/required-docs")
    async def required_docs(vendor_id: str) -> RequiredDocs:
        return await services.required_docs(vendor_id)

    @app.get("/vendors/{vendor_id}/questions")
    async def questions(vendor_id: str) -> list[HumanTask]:
        """Open questions agents are asking this vendor (in-app; the Concierge reads them out)."""
        return await services.hitl.questions(vendor_id)

    @app.post("/vendors/{vendor_id}/questions/{task_id}/answer")
    async def answer_question(vendor_id: str, task_id: str, req: QuestionAnswer) -> HumanTask:
        return await services.hitl.answer(vendor_id, task_id, req.text)

    @app.get("/vendors/{vendor_id}/terms")
    async def terms(vendor_id: str) -> list[VendorTerms]:
        return await services.vendor_terms(vendor_id)

    @app.post("/vendors/{vendor_id}/terms/{terms_id}/{action}")
    async def answer_terms(vendor_id: str, terms_id: str, action: str) -> VendorTerms:
        if action not in ("accept", "decline"):
            raise HTTPException(404, {"error": "unknown_action"})
        return await services.answer_terms(vendor_id, terms_id, action == "accept")

    @app.get("/vendors/{vendor_id}/invites")
    async def invites(vendor_id: str) -> list[VendorInvite]:
        return await services.invites(vendor_id)

    @app.get("/vendors/{vendor_id}/me")
    async def me(vendor_id: str) -> Vendor:
        return await services.vendor(vendor_id)

    @app.patch("/vendors/{vendor_id}/profile")
    async def profile(vendor_id: str, update: ProfileUpdate) -> Vendor:
        return await services.update_profile(vendor_id, update)

    # deals
    @app.get("/vendors/{vendor_id}/deals")
    async def deals(vendor_id: str, language: str | None = None) -> list[DealView]:
        return [await services.deal_view(d, language) for d in await services.deals(vendor_id)]

    @app.get("/vendors/{vendor_id}/deals/{order_id}")
    async def deal(vendor_id: str, order_id: str, language: str | None = None) -> DealView:
        return await services.deal_view_for(vendor_id, order_id, language)

    @app.post("/vendors/{vendor_id}/deals/{order_id}/consent")
    async def consent_deal(vendor_id: str, order_id: str, req: ConsentRequest) -> DealView:
        return await services.consent_to_deal(vendor_id, order_id, req)

    @app.post("/vendors/{vendor_id}/deals/{order_id}/decline")
    async def decline_deal(vendor_id: str, order_id: str) -> DealView:
        return await services.decline_deal(vendor_id, order_id)

    # rules
    @app.get("/vendors/{vendor_id}/rules")
    async def rules(vendor_id: str, language: str | None = None) -> RulesView:
        return await services.rules_view(vendor_id, language)

    @app.post("/vendors/{vendor_id}/rules/draft")
    async def draft_rules(vendor_id: str, draft: RulesDraft) -> RulesView:
        await services.draft_rules(vendor_id, draft)
        return await services.rules_view(vendor_id)

    @app.post("/vendors/{vendor_id}/rules/consent")
    async def consent_rules(vendor_id: str, req: ConsentRequest) -> StandingMandate:
        return await services.consent_to_rules(vendor_id, req)

    # listings
    @app.get("/vendors/{vendor_id}/listings")
    async def listings(vendor_id: str) -> list[Listing]:
        return await services.store.listings.query(vendor_id=vendor_id)

    @app.post("/vendors/{vendor_id}/listings/draft")
    async def draft_listing(vendor_id: str, sub: ListingSubmission) -> ListingDraftView:
        if sub.vendor_id != vendor_id:
            raise HTTPException(400, {"error": "wrong_vendor"})
        return await services.listing_draft_view(vendor_id, sub)

    @app.post("/vendors/{vendor_id}/listings/{listing_id}/confirm")
    async def confirm_listing(
        vendor_id: str, listing_id: str, req: ListingConfirmRequest | None = None
    ) -> Ack:
        image = req.image if req else "original"
        return await services.confirm_listing(vendor_id, listing_id, image=image)

    @app.post("/vendors/{vendor_id}/listings/{listing_id}/withdraw")
    async def withdraw_listing(vendor_id: str, listing_id: str) -> Listing:
        return await services.withdraw_listing(vendor_id, listing_id)

    # onboarding
    @app.post("/vendors/{vendor_id}/documents")
    async def documents(vendor_id: str, upload: DocumentUpload) -> VendorDoc:
        data = base64.b64decode(upload.data_b64) if upload.data_b64 else None
        return await services.submit_document(
            vendor_id, upload.doc_type, upload.number, data, upload.content_type, upload.filename
        )

    @app.post("/vendors/{vendor_id}/apply")
    async def apply(vendor_id: str, retailer_id: str | None = None) -> VendorCheckOut:
        return await services.apply(vendor_id, retailer_id)

    # activity ("who acted for me")
    @app.get("/vendors/{vendor_id}/activity")
    async def activity(vendor_id: str, language: str | None = None) -> list[ActivityItem]:
        vendor = await services.vendor(vendor_id)
        known = Strings(language or vendor.language, settings.vl_config_dir / "i18n").get(
            "activity"
        )
        return [
            ActivityItem(
                ts=r.ts,
                action=r.action,
                text=str(known.get(r.action, r.action)),
                outcome=r.outcome,
                actor=r.actor_agent_id,
            )
            for r in reversed(await services.audit(vendor_id))
        ]

    @app.get("/vendors/{vendor_id}/today")
    async def today(vendor_id: str, language: str | None = None) -> list[str]:
        return await services.today(vendor_id, language)

    # concierge: an agent, reached through the registry like any other
    @app.post("/vendors/{vendor_id}/chat")
    async def chat(vendor_id: str, req: ChatRequest) -> ChatReply:
        payload = ConciergeChatIn(vendor_id=vendor_id, text=req.text, language=req.language)
        return await services.agents.call("concierge.chat", payload, ChatReply)

    @app.post("/vendors/{vendor_id}/chat/photo")
    async def chat_photo(vendor_id: str, upload: DocumentUpload) -> Ack:
        """Attach a photo the next tool call (listing or document) will use."""
        if not upload.data_b64:
            raise HTTPException(422, {"error": "no_photo"})
        payload = ConciergePhotoIn(
            vendor_id=vendor_id, data_b64=upload.data_b64, content_type=upload.content_type
        )
        return await services.agents.call("concierge.attach_photo", payload, Ack)

    @app.websocket("/voice/{vendor_id}")
    async def voice(ws: WebSocket, vendor_id: str) -> None:
        from vendor_svc.voice import run_voice_session

        await run_voice_session(ws, concierge(), vendor_id)

    @app.get("/vendors/{vendor_id}/events")
    async def events(vendor_id: str) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            yield ": connected\n\n"
            async for event, payload in services.bus.subscribe():
                if f'"vendor_id":"{vendor_id}"' in payload.replace(" ", ""):
                    yield f"event: {event}\ndata: {payload}\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"cache-control": "no-cache"}
        )

    # dev only (a hosted demo: with the admin token, see vl_core.devguard)
    @app.post("/dev/tick")
    async def dev_tick(x_vl_admin_token: str | None = Header(default=None)) -> TickOut:
        """Run one Liaison pass now (the background loop does this every VL_HITL_TICK_SECONDS)."""
        require_demo(settings, "vendor.tick", x_vl_admin_token)
        return await tick()

    @app.post("/dev/reset")
    async def dev_reset(
        seed: bool = True, x_vl_admin_token: str | None = Header(default=None)
    ) -> dict[str, list[str]]:
        require_demo(settings, "vendor.reset", x_vl_admin_token)
        await reset_repos(prefix=PREFIX)  # vendor.* includes the Guardian's own collections
        concierge().sessions.clear()
        return {"seeded": await seed_vendors(services) if seed else []}

    return app


app = create_app()
