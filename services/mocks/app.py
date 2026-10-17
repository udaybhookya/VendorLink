from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from mocks.registries import check, fixture
from vl_core.obs import log_startup
from vl_core.obs.tracing import TracingMiddleware
from vl_core.schemas import (
    Ack,
    DemandLine,
    DemandResponse,
    ReceivingReport,
    RegistryCheckRequest,
    RegistryCheckResult,
)
from vl_core.settings import Settings, get_settings

SERVICE = "mocks"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_startup(SERVICE, settings)
    app = FastAPI(title=f"{SERVICE} (simulated external services)")
    app.add_middleware(TracingMiddleware, service=SERVICE)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    received: list[ReceivingReport] = []

    def today() -> date:
        return datetime.now(UTC).date()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"service": SERVICE, "store": settings.vl_store, "signer": settings.vl_signer}

    @app.post("/id/verify")
    async def id_verify(req: RegistryCheckRequest) -> RegistryCheckResult:
        return check("id", req, today())

    @app.post("/bank/verify")
    async def bank_verify(req: RegistryCheckRequest) -> RegistryCheckResult:
        """Penny-drop style: the account exists and the holder name matches."""
        return check("bank", req, today())

    @app.get("/fssai/{number}")
    async def fssai(number: str, name: str) -> RegistryCheckResult:
        return check(
            "fssai", RegistryCheckRequest(doc_type="fssai", number=number, name=name), today()
        )

    @app.get("/pirt/{number}")
    async def pirt(number: str, name: str) -> RegistryCheckResult:
        return check(
            "pirt", RegistryCheckRequest(doc_type="pirt", number=number, name=name), today()
        )

    @app.get("/demand")
    async def demand(store: str, for_date: date) -> DemandResponse:
        lines = fixture("demand").get(store)
        if lines is None:
            raise HTTPException(404, {"error": "unknown_store"})
        return DemandResponse(
            store_id=store,
            for_date=for_date,
            lines=[DemandLine.model_validate(line) for line in lines],
        )

    @app.post("/receiving")
    async def receiving(report: ReceivingReport) -> Ack:
        received.append(report)
        return Ack(detail=f"simulated receiving recorded for {report.order_id}")

    return app


app = create_app()
