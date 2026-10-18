from __future__ import annotations

import hmac
from typing import Any, cast, get_args

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from trust_gateway import observability
from trust_gateway.errors import Denied
from trust_gateway.gateway import Gateway
from vl_core.a2a import GATEWAY_AGENT_ID, GATEWAY_KEY_PATH, HEADER_AGENT
from vl_core.crypto import Signer, get_signer
from vl_core.devguard import require_demo
from vl_core.events import sse_response
from vl_core.obs import log_startup
from vl_core.obs.tracing import TracingMiddleware
from vl_core.schemas import (
    Ack,
    ActivitySummary,
    AgentCredential,
    AgentDetail,
    AgentEvent,
    AgentObservation,
    AgentStatusSnapshot,
    AuditAppend,
    AuditRecord,
    AuditVerifyResult,
    Connection,
    ConsentRecord,
    ControlAction,
    ControlRequest,
    ControlResult,
    CredentialRequest,
    EventBatch,
    HostedAgentsRegistration,
    HumanLoopSummary,
    KeyRotation,
    ObservabilityOverview,
    OrderMandate,
    OrderMandateRequest,
    PartyConfig,
    QualityRow,
    RevokeRequest,
    SafetySummary,
    ScreenRequest,
    ScreenResult,
    SecurityEvent,
    StandingMandate,
    TokenRequest,
    TokenResponse,
    TraceSummary,
    TraceView,
)
from vl_core.settings import Settings, get_settings
from vl_core.store import reset_repos

SERVICE = "trust-gateway"


def create_app(settings: Settings | None = None, signer: Signer | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_startup(SERVICE, settings)
    gw = Gateway(settings, signer or get_signer("gateway", settings))
    app = FastAPI(title=SERVICE)
    app.state.gateway = gw
    app.add_middleware(TracingMiddleware, service=SERVICE)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Denied)
    async def denied(request: Request, exc: Denied) -> JSONResponse:
        await gw.handle_denied(
            exc,
            agent_id=request.headers.get(HEADER_AGENT, "unknown"),
            target=request.url.path,
            body=await request.body(),
        )
        return JSONResponse(
            {"detail": {"error": exc.code, "detail": exc.detail}}, status_code=exc.status
        )

    def require_admin(token: str | None) -> None:
        expected = settings.vl_admin_token
        if not expected or not token or not hmac.compare_digest(token, expected):
            raise HTTPException(403, {"error": "admin_only"})

    def require_operator(token: str | None) -> None:
        expected = settings.vl_operator_token
        if not expected or not token or not hmac.compare_digest(token, expected):
            raise HTTPException(403, {"error": "operator_only"})

    async def signed(request: Request) -> tuple[AgentCredential, bytes]:
        body = await request.body()
        return await gw.authenticate("POST", request.url.path, request.headers, body), body

    @app.get("/healthz")
    @app.get("/health")  # Cloud Run reserves paths ending in "z"; /healthz never reaches us there
    async def healthz() -> dict[str, str]:
        return {"service": SERVICE, "store": settings.vl_store, "signer": settings.vl_signer}

    @app.get(GATEWAY_KEY_PATH)
    async def gateway_key() -> dict[str, str]:
        return {"agent_id": GATEWAY_AGENT_ID, "public_key": await gw.public_key_b64()}

    # identity
    @app.post("/credentials")
    async def credentials(
        req: CredentialRequest, x_vl_admin_token: str | None = Header(default=None)
    ) -> Ack:
        require_admin(x_vl_admin_token)
        await gw.register_credential(req)
        return Ack()

    @app.get("/credentials")
    async def list_credentials() -> list[AgentCredential]:
        return await gw.store.credentials.query()

    @app.post("/agents/{agent_id}/revoke")
    async def revoke(
        agent_id: str, req: RevokeRequest, x_vl_admin_token: str | None = Header(default=None)
    ) -> Ack:
        require_admin(x_vl_admin_token)
        cred = await gw.store.credentials.get(agent_id)
        if cred is None:
            raise HTTPException(404)
        await gw._revoke(cred, req.reason)
        return Ack()

    # ---- agent registry & control plane --------------------------------------------------------
    @app.get("/agents")
    async def agents() -> list[AgentCredential]:
        return await gw.store.credentials.query()

    @app.get("/agents/status")
    async def agents_status() -> AgentStatusSnapshot:
        return await gw.control.snapshot()

    @app.get("/agents/{agent_id}")
    async def agent(agent_id: str) -> AgentCredential:
        cred = await gw.store.credentials.get(agent_id)
        if cred is None:
            raise HTTPException(404, {"error": "unknown_agent"})
        return cred

    @app.post("/agents/hosted")
    async def hosted(request: Request) -> Ack:
        cred, body = await signed(request)
        n = await gw.control.register_hosted(
            cred, HostedAgentsRegistration.model_validate_json(body)
        )
        return Ack(detail=f"{n} agents registered")

    @app.post("/events")
    async def post_events(request: Request) -> Ack:
        cred, body = await signed(request)
        n = await gw.control.ingest(cred, EventBatch.model_validate_json(body))
        return Ack(detail=f"{n} events accepted")

    @app.get("/events/recent")
    async def recent_events(
        agent_id: str | None = None,
        type: str | None = None,
        trace_id: str | None = None,
        limit: int = 200,
    ) -> list[AgentEvent]:
        return gw.control.recent(agent_id=agent_id, type=type, trace_id=trace_id, limit=limit)

    @app.get("/events/stream")
    async def event_stream() -> StreamingResponse:
        return sse_response(gw.bus, only={"agent_event"})

    @app.post("/credentials/rotate")
    async def rotate(request: Request) -> AgentCredential:
        cred, body = await signed(request)
        return await gw.control.rotate(cred, KeyRotation.model_validate_json(body))

    @app.post("/control/agents/{agent_id}/{action}")
    async def control(
        agent_id: str,
        action: str,
        req: ControlRequest,
        x_vl_operator_token: str | None = Header(default=None),
    ) -> ControlResult:
        require_operator(x_vl_operator_token)
        if action not in get_args(ControlAction):
            raise HTTPException(404, {"error": "unknown_action", "detail": action})
        return await gw.control.act(agent_id, cast(ControlAction, action), req)

    @app.post("/control/tenants/{tenant}/{action}")
    async def freeze(
        tenant: str,
        action: str,
        req: ControlRequest,
        x_vl_operator_token: str | None = Header(default=None),
    ) -> ControlResult:
        require_operator(x_vl_operator_token)
        if action not in ("freeze", "unfreeze"):
            raise HTTPException(404, {"error": "unknown_action"})
        return await gw.control.freeze(tenant, action == "freeze", req)

    # ---- observability -------------------------------------------------------------------------
    @app.get("/observability/agents")
    async def observe() -> list[AgentObservation]:
        return await observability.observe_agents(gw)

    @app.get("/observability/overview")
    async def obs_overview() -> ObservabilityOverview:
        return await observability.overview(gw)

    @app.get("/observability/connections")
    async def obs_connections(agent_id: str | None = None) -> list[Connection]:
        return await observability.connections(gw, agent_id)

    @app.get("/observability/activities")
    async def obs_activities() -> ActivitySummary:
        return await observability.activities(gw)

    @app.get("/observability/agents/{agent_id}")
    async def obs_agent(agent_id: str) -> AgentDetail:
        detail = await observability.agent_detail(gw, agent_id)
        if detail is None:
            raise HTTPException(404, {"error": "unknown_agent", "detail": agent_id})
        return detail

    @app.get("/observability/traces")
    async def list_traces(limit: int = 50) -> list[TraceSummary]:
        return observability.traces(gw, limit)

    @app.get("/observability/traces/{trace_id}")
    async def get_trace(trace_id: str) -> TraceView:
        view = observability.trace(gw, trace_id)
        if view is None:
            raise HTTPException(404, {"error": "unknown_trace"})
        return view

    @app.get("/observability/safety")
    async def safety() -> SafetySummary:
        return await observability.safety(gw)

    @app.get("/observability/humans")
    async def humans() -> HumanLoopSummary:
        return observability.humans(gw)

    @app.get("/observability/quality")
    async def quality() -> list[QualityRow]:
        return await observability.quality(gw)

    @app.post("/tokens")
    async def tokens(request: Request) -> TokenResponse:
        cred, body = await signed(request)
        return await gw.issue_token(cred, TokenRequest.model_validate_json(body))

    # relay
    @app.post("/relay/{agent_id}/{skill}")
    async def relay(agent_id: str, skill: str, request: Request) -> JSONResponse:
        result = await gw.relay(agent_id, skill, request.headers, await request.body())
        return JSONResponse(result)

    # audit
    @app.post("/audit")
    async def audit_append(request: Request) -> AuditRecord:
        cred, body = await signed(request)
        entry = AuditAppend.model_validate_json(body)
        gw.control.emit(
            "audit",
            cred.agent_id,
            entry.action,
            on_behalf_of=cred.on_behalf_of,
            target=entry.target,
            outcome=entry.outcome,
        )
        return await gw.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action=entry.action,
            target=entry.target,
            payload_hash=entry.payload_hash,
            outcome=entry.outcome,
            retailer_id=entry.retailer_id,
        )

    @app.get("/audit")
    async def audit_list(party: str | None = None, limit: int = 500) -> list[AuditRecord]:
        return await gw.audit_for(party, limit)

    @app.get("/audit/verify")
    async def audit_verify() -> AuditVerifyResult:
        return await gw.audit.verify()

    @app.get("/security-events")
    async def security_events() -> list[SecurityEvent]:
        return sorted(await gw.store.events.query(), key=lambda e: e.ts)

    @app.get("/events")
    async def events() -> StreamingResponse:
        return sse_response(gw.bus)

    # screening
    @app.post("/screen")
    async def screen(request: Request) -> ScreenResult:
        cred, body = await signed(request)
        return await gw.screen(cred, ScreenRequest.model_validate_json(body))

    # consents, mandates, party config
    @app.post("/consents")
    async def consents(request: Request) -> Ack:
        cred, body = await signed(request)
        await gw.register_consent(cred, ConsentRecord.model_validate_json(body))
        return Ack()

    @app.get("/consents")
    async def list_consents() -> list[ConsentRecord]:
        return await gw.store.consents.query()

    @app.post("/mandates/standing")
    async def standing(request: Request) -> Ack:
        cred, body = await signed(request)
        await gw.put_standing_mandate(cred, StandingMandate.model_validate_json(body))
        return Ack()

    @app.get("/mandates/standing/{vendor_id}")
    async def get_standing(vendor_id: str) -> StandingMandate:
        mandate = await gw.store.standing.get(vendor_id)
        if mandate is None:
            raise HTTPException(404)
        return mandate

    @app.post("/mandates/order")
    async def order_mandate(request: Request) -> Ack:
        cred, body = await signed(request)
        await gw.put_order_mandate(cred, OrderMandateRequest.model_validate_json(body))
        return Ack()

    @app.get("/mandates/order")
    async def list_order_mandates() -> list[OrderMandate]:
        return await gw.store.orders.query()

    @app.post("/parties/config")
    async def party_config(request: Request) -> Ack:
        cred, body = await signed(request)
        await gw.set_party_config(cred, PartyConfig.model_validate_json(body))
        return Ack()

    @app.get("/parties/config")
    async def list_party_configs() -> list[PartyConfig]:
        return await gw.store.parties.query()

    # dev only (a hosted demo: with the admin token, see vl_core.devguard)
    @app.post("/dev/reset")
    async def dev_reset(x_vl_admin_token: str | None = Header(default=None)) -> Ack:
        require_demo(settings, "gateway.reset", x_vl_admin_token)
        await reset_repos(prefix="gw.")
        gw.audit.forget_head()
        gw.control.events.clear()
        return Ack(detail="gateway state cleared")

    @app.post("/dev/tamper/{seq}")
    async def dev_tamper(
        seq: int,
        x_vl_admin_token: str | None = Header(default=None),
        x_vl_operator_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Demo only: edit one stored audit record in place so /audit/verify can catch it."""
        require_demo(settings, "gateway.tamper", x_vl_admin_token, x_vl_operator_token)
        doc_id = f"{seq:012d}"
        record = await gw.store.audit.get(doc_id)
        if record is None:
            raise HTTPException(404)
        await gw.store.audit.put(
            doc_id, record.model_copy(update={"outcome": "ok", "action": "tampered"})
        )
        return {"tampered": seq}

    return app


app = create_app()
