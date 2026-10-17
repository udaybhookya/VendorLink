"""Run any one agent on its own, as a small A2A service.

    python -m agents run produce-grader --port 8101

Endpoints: GET /.well-known/agent.json (public card), POST /invoke/{capability}, GET /healthz.
Skills marked `internal` (e.g. Guardian signing) are never exposed here.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from agents.base import AgentError
from agents.registry import AgentRegistry, RegistryError
from vl_core.errors import Problem
from vl_core.schemas import AgentCard
from vl_core.settings import Settings


def create_agent_app(
    agent_id: str, settings: Settings | None = None, registry: AgentRegistry | None = None
) -> FastAPI:
    registry = registry or AgentRegistry.load(settings)
    agent = registry.agent(agent_id)  # always in-process here, whatever the config says
    card = registry.card(agent_id)
    app = FastAPI(title=f"agent · {card.name}")
    app.state.registry = registry

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"agent": agent_id, "kind": card.kind, "version": card.version}

    @app.get("/.well-known/agent.json")
    async def agent_card(request: Request) -> AgentCard:
        base = str(request.base_url).rstrip("/")
        return card.public().model_copy(
            update={"transport": "a2a", "endpoint": base, "entrypoint": None}
        )

    @app.post("/invoke/{capability}")
    async def invoke(capability: str, request: Request) -> JSONResponse:
        spec = card.skill(capability)
        if spec is None or spec.internal:
            raise HTTPException(404, {"error": "unknown_skill", "detail": capability})
        body: Any = await request.json()
        try:
            result = await agent.invoke(capability, body)
        except AgentError as exc:
            raise HTTPException(422, {"error": "bad_request", "detail": str(exc)}) from exc
        except Problem as exc:
            raise HTTPException(exc.status, {"error": exc.code, "detail": exc.detail}) from exc
        except RegistryError as exc:
            raise HTTPException(502, {"error": "dependency_failed", "detail": str(exc)}) from exc
        return JSONResponse(result.model_dump(mode="json"))

    return app
