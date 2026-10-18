from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.requests import Request

from agents.registry import AgentBlocked
from retailer_svc.catalogue import Catalogue, ImageSize
from retailer_svc.operations import RetailerOperations
from retailer_svc.seed import seed_retailer
from retailer_svc.store import PREFIX, RetailerStore
from retailer_svc.studio import ConfigStudio, config_kind
from vl_core.a2a import GatewayError, GatewayVerifier, a2a_router, party_id_for
from vl_core.config import ConfigError, ConfigLoader
from vl_core.crypto import Signer, get_signer
from vl_core.devguard import require_demo
from vl_core.errors import Problem, error_response
from vl_core.events import sse_response
from vl_core.hitl_http import outbox_router, tick_lifespan
from vl_core.obs import log_startup
from vl_core.obs.tracing import TracingMiddleware
from vl_core.schemas import (
    ActivateRequest,
    ActiveConfig,
    AgentCard,
    AgentStatus,
    CatalogueEntry,
    CatalogueItem,
    ConfigPreset,
    DirectoryEntry,
    HumanTask,
    InfoResponseMessage,
    JobRun,
    Listing,
    PlaceOrderRequest,
    QuestionAnswer,
    QueueItem,
    ReplenishmentRunIn,
    Retailer,
    RetailerOrder,
    RetailerVendor,
    TermsProposal,
    TermsResponse,
    TickIn,
    TickOut,
    VendorInvite,
)
from vl_core.settings import Settings, get_settings
from vl_core.store import get_blob_store, reset_repos

SERVICE = "retailer-svc"


class InviteRequest(BaseModel):
    message: str = ""


class TermsResult(BaseModel):
    terms: TermsProposal
    accepted: bool
    detail: str


def create_app(settings: Settings | None = None, signer: Signer | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_startup(SERVICE, settings)
    store = RetailerStore.create(settings)
    loader = ConfigLoader(settings.vl_config_dir)
    studio = ConfigStudio(loader, store)
    orch = RetailerOperations(
        settings,
        signer or get_signer("retailer", settings),
        store,
        studio,
        get_blob_store(settings),
    )

    async def tick() -> TickOut:
        return await orch.agents.call("human.tick", TickIn(), TickOut)

    def channel(name: str) -> Any:
        r = next((r for r in orch.agents.registered() if r.card.agent_id == name), None)
        return orch.agents.agent(name) if r is not None and r.route == "local" else None

    app = FastAPI(title=SERVICE, lifespan=tick_lifespan(tick, settings))
    app.state.operations = orch
    app.include_router(outbox_router(channel, settings))
    app.add_middleware(TracingMiddleware, service=SERVICE)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Problem)
    async def problem(_: Request, exc: Problem) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.detail)

    @app.exception_handler(ConfigError)
    async def config_error(_: Request, exc: ConfigError) -> JSONResponse:
        return error_response(422, "bad_config", str(exc))

    @app.exception_handler(AgentBlocked)
    async def agent_blocked(_: Request, exc: AgentBlocked) -> JSONResponse:
        return error_response(423, "agent_blocked", str(exc))

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        status = exc.status if 400 <= exc.status < 500 else 502
        return error_response(status, exc.code, exc.detail)

    async def owns(agent_id: str) -> bool:
        try:
            return await store.retailers.get(party_id_for(agent_id)) is not None
        except ValueError:
            return False

    app.include_router(
        a2a_router(
            GatewayVerifier(settings.trust_gateway_url),
            {
                "vendor.onboard": orch.on_vendor_onboard,
                "listing.submit": orch.on_listing_submit,
                "listing.confirm": orch.on_listing_confirm,
                "order.vendor_signed": orch.on_vendor_signed,
                "order.vendor_declined": orch.on_vendor_declined,
                "info.response": orch.hitl.on_info_response,
                "terms.response": orch.hitl.on_terms_response,
            },
            owns,
        )
    )

    @app.get("/healthz")
    @app.get("/health")  # Cloud Run reserves paths ending in "z"; /healthz never reaches us there
    async def healthz() -> dict[str, str]:
        return {"service": SERVICE, "store": settings.vl_store, "signer": settings.vl_signer}

    @app.get("/retailers")
    async def retailers() -> list[Retailer]:
        return await store.retailers.query()

    # configuration studio (M7)
    @app.get("/config/presets")
    async def presets() -> list[ConfigPreset]:
        return [
            loader.load_preset(p.stem) for p in sorted((loader.root / "presets").glob("*.json"))
        ]

    @app.get("/config/{kind}")
    async def config_list(kind: str) -> list[dict[str, Any]]:
        k = config_kind(kind)
        return [studio.load(k, cid).model_dump(mode="json") for cid in studio.ids(k)]

    @app.get("/config/{kind}/{config_id}")
    async def config_get(kind: str, config_id: str, version: int | None = None) -> dict[str, Any]:
        return studio.load(config_kind(kind), config_id, version).model_dump(mode="json")

    @app.put("/config/{kind}/{config_id}")
    async def config_put(kind: str, config_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Saves a new version; nothing changes until a config is activated."""
        return (await studio.save(config_kind(kind), config_id, body)).model_dump(mode="json")

    @app.get("/registry/cards")
    async def cards() -> list[AgentCard]:
        return orch.agents.cards

    @app.get("/registry/agents")
    async def registered_agents() -> list[AgentStatus]:
        """Every registered agent and how this host reaches it (local, a2a or unavailable)."""
        return [
            AgentStatus(card=r.card, route=r.route, hosts=r.entry.hosts, enabled=r.entry.enabled)
            for r in orch.agents.registered()
        ]

    @app.get("/retailers/{retailer_id}/config/active")
    async def active(retailer_id: str) -> ActiveConfig:
        return await orch.active(retailer_id)

    @app.post("/retailers/{retailer_id}/config/activate")
    async def activate(retailer_id: str, req: ActivateRequest) -> ActiveConfig:
        refs = req.refs or (loader.load_preset(req.preset).refs if req.preset else None)
        if refs is None:
            raise HTTPException(422, {"error": "preset_or_refs_required"})
        return await orch.activate(retailer_id, refs, req.preset)

    # operations
    @app.get("/retailers/{retailer_id}/orders")
    async def orders(retailer_id: str) -> list[RetailerOrder]:
        return sorted(
            await store.orders.query(retailer_id=retailer_id),
            key=lambda o: o.updated_at,
            reverse=True,
        )

    @app.post("/retailers/{retailer_id}/orders/place")
    async def place(retailer_id: str, req: PlaceOrderRequest) -> list[RetailerOrder]:
        return await orch.place_order(retailer_id, req.vendor_id, req.store_id, req.for_date)

    @app.get("/retailers/{retailer_id}/queue")
    async def queue(retailer_id: str) -> list[QueueItem]:
        return sorted(
            await store.queue.query(retailer_id=retailer_id),
            key=lambda q: q.created_at,
            reverse=True,
        )

    @app.post("/retailers/{retailer_id}/queue/{item_id}/{action}")
    async def resolve(retailer_id: str, item_id: str, action: str) -> QueueItem:
        if action not in ("approve", "reject"):
            raise HTTPException(404)
        return await orch.resolve(retailer_id, item_id, approve=action == "approve")

    @app.get("/retailers/{retailer_id}/vendors")
    async def vendors(retailer_id: str) -> list[RetailerVendor]:
        await orch.active(retailer_id)
        return await store.vendors.query()

    @app.get("/retailers/{retailer_id}/listings")
    async def listings(retailer_id: str) -> list[Listing]:
        await orch.active(retailer_id)
        return await store.listings.query()

    # catalogue: products with their photos, copy, history and orders
    catalogue = Catalogue(orch)

    @app.get("/retailers/{retailer_id}/catalogue")
    async def catalogue_items(
        retailer_id: str,
        status: str | None = None,
        vendor_id: str | None = None,
        sku: str | None = None,
    ) -> list[CatalogueItem]:
        return await catalogue.items(retailer_id, status, vendor_id, sku)

    @app.get("/retailers/{retailer_id}/catalogue/{listing_id}")
    async def catalogue_entry(retailer_id: str, listing_id: str) -> CatalogueEntry:
        return await catalogue.entry(retailer_id, listing_id)

    @app.get("/retailers/{retailer_id}/catalogue/{listing_id}/images/{image_id}")
    async def catalogue_image(
        retailer_id: str, listing_id: str, image_id: str, size: ImageSize = "full"
    ) -> Response:
        data, mime = await catalogue.image(retailer_id, listing_id, image_id, size)
        return Response(data, media_type=mime, headers={"cache-control": "private, max-age=300"})

    @app.post("/retailers/{retailer_id}/terms/{vendor_id}")
    async def terms(retailer_id: str, vendor_id: str) -> TermsResult:
        proposal, ack = await orch.propose_terms(retailer_id, vendor_id)
        return TermsResult(terms=proposal, accepted=ack.ok, detail=ack.detail)

    @app.get("/retailers/{retailer_id}/directory")
    async def directory(retailer_id: str) -> list[DirectoryEntry]:
        return await orch.directory(retailer_id)

    @app.post("/retailers/{retailer_id}/suppliers/{vendor_id}/invite")
    async def invite(retailer_id: str, vendor_id: str, req: InviteRequest) -> VendorInvite:
        return await orch.invite(retailer_id, vendor_id, req.message)

    # human in the loop
    @app.get("/retailers/{retailer_id}/tasks")
    async def tasks(retailer_id: str, status: str | None = None) -> list[HumanTask]:
        return await orch.hitl.tasks(retailer_id, status)

    @app.post("/retailers/{retailer_id}/tasks/{task_id}/answer")
    async def answer_task(retailer_id: str, task_id: str, req: QuestionAnswer) -> HumanTask:
        return await orch.hitl.answer(retailer_id, task_id, req.text, req.by)

    @app.get("/retailers/{retailer_id}/terms-responses")
    async def terms_responses(retailer_id: str) -> list[TermsResponse]:
        return await orch.hitl.terms.query(retailer_id=retailer_id)

    @app.get("/retailers/{retailer_id}/info-responses")
    async def info_responses(retailer_id: str) -> list[InfoResponseMessage]:
        return await orch.hitl.responses.query(retailer_id=retailer_id)

    # durable batch jobs (LangGraph)
    @app.post("/retailers/{retailer_id}/jobs/replenishment-run")
    async def replenishment_run(retailer_id: str, req: ReplenishmentRunIn) -> JobRun:
        """Plan every store, ask people where needed, send. Cloud Scheduler calls this nightly."""
        return await orch.jobs.start(retailer_id, req)

    @app.get("/retailers/{retailer_id}/jobs")
    async def jobs(retailer_id: str) -> list[JobRun]:
        runs = await orch.jobs.jobs.query(retailer_id=retailer_id)
        return sorted(runs, key=lambda r: r.started_at, reverse=True)

    @app.get("/retailers/{retailer_id}/jobs/{job_id}")
    async def job(retailer_id: str, job_id: str) -> JobRun:
        run = await orch.jobs.jobs.get(job_id)
        if run is None or run.retailer_id != retailer_id:
            raise HTTPException(404, {"error": "unknown_job"})
        return run

    @app.post("/dev/tick")
    async def dev_tick(x_vl_admin_token: str | None = Header(default=None)) -> TickOut:
        require_demo(settings, "retailer.tick", x_vl_admin_token)
        return await tick()

    @app.get("/retailers/{retailer_id}/events")
    async def events(retailer_id: str) -> StreamingResponse:
        return sse_response(orch.bus)

    # dev only (a hosted demo: with the admin token, see vl_core.devguard)
    @app.post("/dev/reset")
    async def dev_reset(
        preset: str | None = None, x_vl_admin_token: str | None = Header(default=None)
    ) -> dict[str, str]:
        require_demo(settings, "retailer.reset", x_vl_admin_token)
        await reset_repos(prefix=PREFIX)
        await studio.refresh()
        chosen = preset or settings.active_config
        return {"retailer": await seed_retailer(orch, chosen), "preset": chosen}

    return app


app = create_app()
