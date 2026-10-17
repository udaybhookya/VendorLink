"""Receiving side of A2A: accept only requests the trust gateway forwarded and signed."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from vl_core.a2a.http import make_client
from vl_core.a2a.signing import HEADER_AGENT, HEADER_JTI, HEADER_SIGNATURE, verify_request
from vl_core.schemas import A2AEnvelope

GATEWAY_AGENT_ID = "platform:gateway"
GATEWAY_KEY_PATH = "/.well-known/gateway-key"

SkillHandler = Callable[[A2AEnvelope], Awaitable[BaseModel]]


class GatewayVerifier:
    def __init__(self, gateway_url: str) -> None:
        self._gateway_url = gateway_url
        self._key: str | None = None

    async def _public_key(self) -> str:
        if self._key is None:
            async with make_client(self._gateway_url) as client:
                response = await client.get(GATEWAY_KEY_PATH)
                response.raise_for_status()
                self._key = str(response.json()["public_key"])
        return self._key

    async def verify(self, request: Request, body: bytes) -> None:
        agent = request.headers.get(HEADER_AGENT)
        jti = request.headers.get(HEADER_JTI, "")
        signature = request.headers.get(HEADER_SIGNATURE, "")
        if agent != GATEWAY_AGENT_ID or not verify_request(
            await self._public_key(), "POST", request.url.path, body, jti, signature
        ):
            raise HTTPException(401, {"error": "not_from_gateway", "detail": "direct call refused"})


def a2a_router(
    verifier: GatewayVerifier,
    handlers: dict[str, SkillHandler],
    owns_agent: Callable[[str], Awaitable[bool]],
) -> APIRouter:
    """`POST /a2a/{agent_id}/{skill}`; dispatches the envelope to `handlers[skill]`."""
    router = APIRouter()

    @router.post("/a2a/{agent_id}/{skill}")
    async def receive(agent_id: str, skill: str, request: Request) -> JSONResponse:
        body = await request.body()
        await verifier.verify(request, body)
        try:
            envelope = A2AEnvelope.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, {"error": "bad_envelope", "detail": str(exc)}) from exc
        if envelope.to_agent != agent_id or envelope.skill != skill:
            raise HTTPException(400, {"error": "envelope_mismatch"})
        handler = handlers.get(skill)
        if handler is None or not await owns_agent(agent_id):
            raise HTTPException(404, {"error": "unknown_skill_or_agent"})
        result = await handler(envelope)
        return JSONResponse(result.model_dump(mode="json"))

    return router
