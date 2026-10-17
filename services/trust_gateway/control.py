"""The gateway as agent control plane: agent records, one event feed, and control actions.

Every action names the person who took it and lands in both the event feed (operational) and
the hash-chained audit log (formal record).
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from trust_gateway.errors import Denied
from vl_core.crypto import b64d, sha256_hex
from vl_core.obs.tracing import current_ids
from vl_core.schemas import (
    AgentCredential,
    AgentEvent,
    AgentState,
    AgentStatusEntry,
    AgentStatusSnapshot,
    ControlAction,
    ControlRequest,
    ControlResult,
    EventBatch,
    EventType,
    HostedAgentsRegistration,
    KeyRotation,
    TenantFreeze,
)
from vl_core.schemas.control import Scalar

if TYPE_CHECKING:
    from trust_gateway.gateway import Gateway

EVENT_BUFFER = 20_000
LAST_SEEN_EVERY_S = 5.0


def fingerprint(public_key_b64: str) -> str:
    digest = sha256_hex(b64d(public_key_b64))[:16]
    return "SHA256:" + ":".join(digest[i : i + 4] for i in range(0, 16, 4))


class ControlPlane:
    def __init__(self, gw: Gateway) -> None:
        self.gw = gw
        self.events: deque[AgentEvent] = deque(maxlen=EVENT_BUFFER)
        self._last_seen_written: dict[str, float] = {}

    # ---- events -------------------------------------------------------------------------------

    def emit(
        self,
        type: EventType,
        agent_id: str,
        action: str,
        *,
        on_behalf_of: str = "",
        target: str = "",
        outcome: Literal["ok", "denied", "error"] = "ok",
        latency_ms: float | None = None,
        actor: str | None = None,
        attrs: dict[str, Scalar] | None = None,
    ) -> AgentEvent:
        trace_id, span_id = current_ids()
        event = AgentEvent(
            id=uuid.uuid4().hex,
            ts=datetime.now(UTC),
            type=type,
            agent_id=agent_id,
            on_behalf_of=on_behalf_of,
            action=action,
            target=target,
            outcome=outcome,
            latency_ms=latency_ms,
            trace_id=trace_id,
            span_id=span_id,
            actor=actor,
            attrs=attrs or {},
        )
        self._push(event)
        return event

    def _push(self, event: AgentEvent) -> None:
        self.events.append(event)
        self.gw.bus.publish("agent_event", event)

    async def ingest(self, host: AgentCredential, batch: EventBatch) -> int:
        """Events about in-process agents, reported by the host that runs them."""
        accepted = 0
        for event in batch.events:
            record = await self.gw.store.credentials.get(event.agent_id)
            if record is None or record.host != host.host:
                continue  # a host may only report on its own agents
            self._push(event)
            await self.touch(record)
            accepted += 1
        return accepted

    def recent(
        self,
        *,
        agent_id: str | None = None,
        type: str | None = None,
        trace_id: str | None = None,
        limit: int = 200,
    ) -> list[AgentEvent]:
        out = [
            e
            for e in reversed(self.events)
            if (agent_id is None or e.agent_id == agent_id or e.target == agent_id)
            and (type is None or e.type == type)
            and (trace_id is None or e.trace_id == trace_id)
        ]
        return out[:limit]

    # ---- agent records ------------------------------------------------------------------------

    async def touch(self, cred: AgentCredential) -> None:
        now = self.gw.clock()
        if now - self._last_seen_written.get(cred.agent_id, 0.0) < LAST_SEEN_EVERY_S:
            return
        self._last_seen_written[cred.agent_id] = now
        fresh = await self.gw.store.credentials.get(cred.agent_id) or cred
        await self.gw.store.credentials.put(
            cred.agent_id, fresh.model_copy(update={"last_seen": datetime.now(UTC)})
        )

    async def frozen(self) -> list[str]:
        return sorted(f.tenant for f in await self.gw.store.freezes.query() if f.frozen)

    async def effective(self, cred: AgentCredential, frozen: list[str] | None = None) -> AgentState:
        """Status after tenant freezes and subject quarantine are applied."""
        if cred.status != "active":
            return cred.status
        frozen = frozen if frozen is not None else await self.frozen()
        if cred.party_type in frozen or cred.on_behalf_of in frozen:
            return "suspended"
        if await self.gw.is_quarantined(cred.on_behalf_of):
            return "quarantined"
        return "active"

    async def snapshot(self) -> AgentStatusSnapshot:
        frozen = await self.frozen()
        entries = [
            AgentStatusEntry(
                agent_id=c.agent_id,
                status=c.status,
                effective=await self.effective(c, frozen),
                permissions=c.permissions,
                rate_limit_per_min=c.rate_limit_per_min,
                key_rotation_requested=c.key_rotation_requested,
            )
            for c in await self.gw.store.credentials.query()
        ]
        return AgentStatusSnapshot(agents=entries, frozen=frozen)

    async def register_hosted(self, host: AgentCredential, reg: HostedAgentsRegistration) -> int:
        if host.kind != "host" or host.host != reg.host:
            raise Denied(
                403, "not_a_host", f"{host.agent_id} cannot register agents for {reg.host}"
            )
        for agent in reg.agents:
            existing = await self.gw.store.credentials.get(agent.agent_id)
            base = existing or AgentCredential(
                agent_id=agent.agent_id,
                party_type=agent.tenant,
                kind=agent.kind,
                on_behalf_of=agent.on_behalf_of,
                public_key=host.public_key,
                issued_at=datetime.now(UTC),
                permissions=agent.permissions,
            )
            # Re-registration refreshes identity, never an operator's status or overrides.
            record = base.model_copy(
                update={
                    "kind": agent.kind,
                    "host": reg.host,
                    "display_name": agent.display_name,
                    "public_key": host.public_key,
                    "key_fingerprint": host.key_fingerprint,
                    "last_seen": datetime.now(UTC),
                }
            )
            await self.gw.store.credentials.put(agent.agent_id, record)
            if existing is None:
                await self.gw.record(
                    actor=host.agent_id,
                    on_behalf_of=agent.on_behalf_of,
                    action="agent.register",
                    target=agent.agent_id,
                    payload=record,
                )
        return len(reg.agents)

    # ---- control actions ----------------------------------------------------------------------

    async def _audit_control(
        self, action: str, target: str, req: ControlRequest | Any, payload: Any
    ) -> None:
        actor = getattr(req, "actor", "unknown")
        await self.gw.record(
            actor=f"operator:{actor}",
            on_behalf_of="platform",
            action=f"control.{action}",
            target=target,
            payload=payload,
        )
        self.emit(
            "control",
            f"operator:{actor}",
            action,
            target=target,
            actor=actor,
            attrs={"reason": getattr(req, "reason", "")},
        )

    async def act(self, agent_id: str, action: ControlAction, req: ControlRequest) -> ControlResult:

        cred = await self.gw.store.credentials.get(agent_id)
        if cred is None:
            raise Denied(404, "unknown_agent", agent_id)
        now = datetime.now(UTC)
        changes: dict[str, Any] = {}
        if action == "suspend":
            changes = {"status": "suspended", "status_reason": req.reason}
        elif action == "resume":
            if cred.status == "revoked":
                raise Denied(409, "revoked_is_final", "issue a new credential instead")
            changes = {"status": "active", "status_reason": ""}
        elif action == "quarantine":
            changes = {"status": "quarantined", "status_reason": req.reason}
        elif action == "revoke":
            changes = {"status": "revoked", "status_reason": req.reason, "revoked_at": now}
        elif action == "rotate_key":
            if cred.kind not in ("party", "host"):
                raise Denied(409, "no_own_key", "hosted agents act under their host's key")
            changes = {"key_rotation_requested": True}
        elif action == "set_permissions":
            if req.permissions is None:
                raise Denied(422, "permissions_required")
            changes = {"permissions": req.permissions}
        elif action == "set_rate_limit":
            if req.rate_limit_per_min is None:
                raise Denied(422, "rate_limit_required")
            changes = {"rate_limit_per_min": req.rate_limit_per_min}
        elif action == "end_sessions":
            changes = {"sessions_valid_after": now}
        updated = cred.model_copy(update=changes)
        await self.gw.store.credentials.put(agent_id, updated)
        await self._audit_control(action, agent_id, req, {"changes": changes, "reason": req.reason})
        return ControlResult(agent=updated, detail=f"{action} applied")

    async def freeze(self, tenant: str, frozen: bool, req: ControlRequest) -> ControlResult:
        record = TenantFreeze(
            tenant=tenant, frozen=frozen, actor=req.actor, reason=req.reason, ts=datetime.now(UTC)
        )
        await self.gw.store.freezes.put(tenant, record)
        await self._audit_control(
            "freeze_tenant" if frozen else "unfreeze_tenant", tenant, req, record
        )
        return ControlResult(frozen=await self.frozen(), detail=f"{tenant} frozen={frozen}")

    async def rotate(self, cred: AgentCredential, req: KeyRotation) -> AgentCredential:
        """The agent itself (signing with its old key) installs its new key."""

        try:
            if len(b64d(req.public_key)) != 32:
                raise ValueError
        except ValueError as exc:
            raise Denied(422, "bad_public_key") from exc
        updated = cred.model_copy(
            update={
                "public_key": req.public_key,
                "key_fingerprint": fingerprint(req.public_key),
                "key_rotation_requested": False,
                "sessions_valid_after": datetime.now(UTC),
            }
        )
        await self.gw.store.credentials.put(cred.agent_id, updated)
        await self.gw.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action="credential.rotate",
            target=cred.agent_id,
            payload={"fingerprint": updated.key_fingerprint},
        )
        self.emit(
            "control",
            cred.agent_id,
            "rotate_key",
            target=cred.agent_id,
            attrs={"fingerprint": updated.key_fingerprint},
        )
        return updated
