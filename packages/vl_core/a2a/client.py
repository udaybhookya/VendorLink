"""Signed client for the trust gateway. Every cross-tenant call goes through here."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from pydantic import BaseModel

from vl_core.a2a.http import make_client
from vl_core.a2a.signing import (
    HEADER_AGENT,
    HEADER_JTI,
    HEADER_SIGNATURE,
    make_envelope,
    sign_request,
)
from vl_core.crypto import Signer, b64e, canonical_json, hash_obj
from vl_core.obs import get_logger
from vl_core.schemas import (
    Ack,
    AgentCredential,
    AgentStatusSnapshot,
    AuditAppend,
    ConsentRecord,
    CredentialRequest,
    EventBatch,
    HostedAgentsRegistration,
    KeyRotation,
    OrderMandateRequest,
    PartyConfig,
    ScreenRequest,
    ScreenResult,
    StandingMandate,
    TokenRequest,
    TokenResponse,
)
from vl_core.settings import get_settings

ADMIN_HEADER = "X-VL-Admin-Token"


log = get_logger("gateway.client")


class GatewayError(Exception):
    def __init__(self, status: int, code: str, detail: str = "") -> None:
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        data = response.json()
        body = data.get("detail", data) if isinstance(data, dict) else {}
        code = str(body.get("error", "error")) if isinstance(body, dict) else "error"
        detail = str(body.get("detail", "")) if isinstance(body, dict) else str(body)
    except ValueError:
        code, detail = "error", response.text
    raise GatewayError(response.status_code, code, detail)


class GatewayClient:
    def __init__(self, base_url: str, agent_id: str, signer: Signer) -> None:
        self.base_url = base_url
        self.agent_id = agent_id
        self._signer = signer

    def client(self) -> httpx.AsyncClient:
        """Plain client for the gateway's public read endpoints."""
        return make_client(self.base_url)

    async def public_key_b64(self) -> str:
        return b64e(await self._signer.public_key(self.agent_id))

    async def _post(
        self,
        path: str,
        body: BaseModel,
        *,
        jti: str | None = None,
        token: str | None = None,
        wait_seconds: float = 30.0,
    ) -> httpx.Response:
        raw = canonical_json(body)
        jti = jti or uuid.uuid4().hex
        headers = {
            "content-type": "application/json",
            HEADER_AGENT: self.agent_id,
            HEADER_JTI: jti,
            HEADER_SIGNATURE: await sign_request(
                self._signer, self.agent_id, "POST", path, raw, jti
            ),
        }
        if token:
            headers["authorization"] = f"Bearer {token}"
        async with make_client(self.base_url, timeout=wait_seconds) as client:
            response = await client.post(path, content=raw, headers=headers)
        try:
            _raise_for(response)
        except GatewayError as exc:
            log.warning(
                f"gateway refused POST {path}",
                agent=self.agent_id,
                status=exc.status,
                error=exc.code,
                detail=exc.detail[:200] or None,
            )
            raise
        return response

    async def token(self, scope: str, aud: str) -> TokenResponse:
        response = await self._post("/tokens", TokenRequest(scope=scope, aud=aud))
        return TokenResponse.model_validate(response.json())

    async def relay[T: BaseModel](
        self,
        to_agent: str,
        skill: str,
        body: BaseModel,
        *,
        on_behalf_of: str,
        response_model: type[T],
    ) -> T:
        """Get a single-use token for (skill, to_agent), then send through the gateway relay."""
        token = await self.token(skill, to_agent)
        envelope = make_envelope(
            skill=skill,
            from_agent=self.agent_id,
            to_agent=to_agent,
            on_behalf_of=on_behalf_of,
            body=body,
            jti=token.jti,
        )
        response = await self._post(
            f"/relay/{to_agent}/{skill}",
            envelope,
            jti=token.jti,
            token=token.token,
            wait_seconds=get_settings().vl_relay_timeout_seconds,
        )
        return response_model.model_validate(response.json())

    async def audit(
        self,
        action: str,
        target: str,
        payload: Any,
        *,
        outcome: str = "ok",
        retailer_id: str | None = None,
    ) -> None:
        body = AuditAppend.model_validate(
            {
                "action": action,
                "target": target,
                "payload_hash": hash_obj(payload),
                "outcome": outcome,
                "retailer_id": retailer_id,
            }
        )
        await self._post("/audit", body)

    async def screen(self, request: ScreenRequest) -> ScreenResult:
        return ScreenResult.model_validate((await self._post("/screen", request)).json())

    async def register_consent(self, consent: ConsentRecord) -> Ack:
        return Ack.model_validate((await self._post("/consents", consent)).json())

    async def post_standing_mandate(self, mandate: StandingMandate) -> Ack:
        return Ack.model_validate((await self._post("/mandates/standing", mandate)).json())

    async def post_order_mandate(self, request: OrderMandateRequest) -> Ack:
        return Ack.model_validate((await self._post("/mandates/order", request)).json())

    async def post_events(self, batch: EventBatch) -> Ack:
        return Ack.model_validate((await self._post("/events", batch)).json())

    async def register_hosted(self, registration: HostedAgentsRegistration) -> Ack:
        return Ack.model_validate((await self._post("/agents/hosted", registration)).json())

    async def rotate_key(self, rotation: KeyRotation) -> AgentCredential:
        return AgentCredential.model_validate(
            (await self._post("/credentials/rotate", rotation)).json()
        )

    async def status_snapshot(self) -> AgentStatusSnapshot:
        async with make_client(self.base_url) as client:
            response = await client.get("/agents/status")
        _raise_for(response)
        return AgentStatusSnapshot.model_validate(response.json())

    async def set_party_config(self, config: PartyConfig) -> Ack:
        return Ack.model_validate((await self._post("/parties/config", config)).json())


async def register_credential(base_url: str, admin_token: str, request: CredentialRequest) -> Ack:
    """Bootstrap an agent credential (on vendor approval or at seed time)."""
    async with make_client(base_url) as client:
        response = await client.post(
            "/credentials",
            content=canonical_json(request),
            headers={"content-type": "application/json", ADMIN_HEADER: admin_token},
        )
    _raise_for(response)
    return Ack.model_validate(response.json())
