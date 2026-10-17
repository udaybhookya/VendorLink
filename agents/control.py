"""A host's link to the gateway control plane.

Each deployable that runs agents in-process (vendor-svc, retailer-svc) registers them with the
gateway, reports their calls and decisions as events, keeps a cached status snapshot so the
registry can refuse a suspended agent before running it, and rotates keys when asked.

If the gateway cannot be reached, the last known snapshot is used; with none yet, calls are
allowed and the failure is logged (availability over control for the MVP; see ADR 0003).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from vl_core.a2a import GatewayClient, GatewayError, register_credential
from vl_core.crypto import Signer, b64e
from vl_core.obs import get_logger
from vl_core.schemas import (
    AgentEvent,
    AgentStatusEntry,
    AgentStatusSnapshot,
    CredentialRequest,
    EventBatch,
    HostedAgent,
    HostedAgentsRegistration,
    KeyRotation,
)
from vl_core.settings import Settings

if TYPE_CHECKING:
    from agents.registry import AgentRegistry

log = get_logger("agents.control")


class HostControl:
    def __init__(
        self,
        settings: Settings,
        host: str,
        signer: Signer,
        *,
        ttl: float = 2.0,
        owns_key: Callable[[str], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.host = host
        self.agent_id = f"host:{host}"
        self.ttl = ttl
        self._signer = signer
        self._owns_key = owns_key or (lambda agent_id: False)
        self.client = GatewayClient(settings.trust_gateway_url, self.agent_id, signer)
        self.registry: AgentRegistry | None = None
        self._snapshot: AgentStatusSnapshot | None = None
        self._fetched_at = 0.0
        self._registered = False

    def reset(self) -> None:
        """Forget registration and status (after a gateway reset)."""
        self._registered = False
        self._snapshot = None
        self._fetched_at = 0.0

    async def ensure_registered(self) -> None:
        if self._registered:
            return
        await register_credential(
            self.settings.trust_gateway_url,
            self.settings.vl_admin_token,
            CredentialRequest(
                agent_id=self.agent_id,
                party_type="platform",
                on_behalf_of="platform",
                kind="host",
                display_name=self.host,
                public_key=await self.client.public_key_b64(),
            ),
        )
        agents = []
        if self.registry is not None:
            for r in self.registry.registered():
                if r.route != "local":
                    continue
                tenant = r.card.tenant if r.card.tenant != "any" else self.registry.tenant
                agents.append(
                    HostedAgent(
                        agent_id=self.registry.gateway_id(r.card),
                        kind=r.card.kind,
                        tenant=tenant,
                        on_behalf_of=f"tenant:{tenant}",
                        permissions=r.card.capabilities,
                        display_name=r.card.name,
                    )
                )
        await self.client.register_hosted(HostedAgentsRegistration(host=self.host, agents=agents))
        self._registered = True

    async def _with_registration[T](self, call: Callable[[], Awaitable[T]]) -> T:
        await self.ensure_registered()
        try:
            return await call()
        except GatewayError as exc:
            if exc.code not in ("bad_signature", "not_a_host"):
                raise
            self.reset()  # the gateway forgot us (restart or reset): register again, once
            await self.ensure_registered()
            return await call()

    async def status(self, agent_id: str) -> AgentStatusEntry | None:
        now = time.monotonic()
        if self._snapshot is None or now - self._fetched_at >= self.ttl:
            try:
                await self.ensure_registered()
                self._snapshot = await self.client.status_snapshot()
                self._fetched_at = now
                await self._rotate_requested()
            except Exception as exc:  # gateway unreachable: keep the last snapshot
                log.warning("status refresh failed", extra={"fields": {"error": repr(exc)}})
        if self._snapshot is None:
            return None
        return next((a for a in self._snapshot.agents if a.agent_id == agent_id), None)

    async def report(self, event: AgentEvent) -> None:
        try:
            await self._with_registration(
                lambda: self.client.post_events(EventBatch(events=[event]))
            )
        except Exception as exc:  # telemetry must never break a business call
            log.warning("event report failed", extra={"fields": {"error": repr(exc)}})

    async def _rotate_requested(self) -> None:
        assert self._snapshot is not None
        for entry in self._snapshot.agents:
            if entry.key_rotation_requested and (
                entry.agent_id == self.agent_id or self._owns_key(entry.agent_id)
            ):
                await self.rotate(entry.agent_id)

    async def rotate(self, agent_id: str) -> None:
        """New key under a staging id → announce it signed with the old key → promote it."""
        staging = f"{agent_id}#next"
        new_public = b64e(await self._signer.public_key(staging))
        await GatewayClient(self.settings.trust_gateway_url, agent_id, self._signer).rotate_key(
            KeyRotation(public_key=new_public)
        )
        await self._signer.promote(staging, agent_id)
        log.info("key rotated", extra={"fields": {"agent_id": agent_id}})
