"""Trust gateway logic. Routes in app.py stay thin; every decision is made here."""

from __future__ import annotations

import base64
import binascii
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from trust_gateway.audit import AuditLog
from trust_gateway.control import ControlPlane, fingerprint
from trust_gateway.errors import Denied, EventType
from trust_gateway.policy import SKILL_POLICY
from vl_core.a2a import (
    GATEWAY_AGENT_ID,
    HEADER_AGENT,
    HEADER_JTI,
    HEADER_SIGNATURE,
    make_client,
    sign_request,
    verify_request,
)
from vl_core.config import ConfigLoader
from vl_core.crypto import (
    JwtError,
    Signer,
    b64d,
    b64e,
    canonical_json,
    hash_obj,
    jwt_decode,
    jwt_encode,
    sha256_hex,
    verify_signature,
)
from vl_core.events import Broadcaster
from vl_core.obs import get_logger
from vl_core.schemas import (
    A2AEnvelope,
    AgentCredential,
    AuditRecord,
    ConsentRecord,
    CredentialRequest,
    OrderMandate,
    OrderMandateRequest,
    PartyConfig,
    ScreenRequest,
    ScreenResult,
    SecurityEvent,
    StandingMandate,
    TenantFreeze,
    TokenRequest,
    TokenResponse,
)
from vl_core.screening import UNAVAILABLE, body_text, get_screener
from vl_core.settings import Settings
from vl_core.store import Repo, get_repo

log = get_logger("gateway")

NO_CONFIG = "none"


# Audit actions the gateway itself decides, logged at INFO (the rest at DEBUG; refusals warn).
GATEWAY_EVENTS = (
    "consent.",
    "mandate.",
    "credential.revoke",
    "credential.rotate",
    "control.",
    "screen.",
)


class UsedJti(BaseModel):
    jti: str
    ts: float


class BlobHash(BaseModel):
    sha256: str
    subject: str


class Quarantine(BaseModel):
    subject: str
    reason: str
    ts: datetime


@dataclass
class Store:
    credentials: Repo[AgentCredential]
    audit: Repo[AuditRecord]
    events: Repo[SecurityEvent]
    used_jti: Repo[UsedJti]
    consents: Repo[ConsentRecord]
    standing: Repo[StandingMandate]
    orders: Repo[OrderMandate]
    parties: Repo[PartyConfig]
    blobs: Repo[BlobHash]
    quarantine: Repo[Quarantine]
    freezes: Repo[TenantFreeze]

    @classmethod
    def create(cls, settings: Settings) -> Store:
        def repo[T: BaseModel](model: type[T], name: str) -> Repo[T]:
            return get_repo(model, f"gw.{name}", settings)

        return cls(
            credentials=repo(AgentCredential, "credentials"),
            audit=repo(AuditRecord, "audit"),
            events=repo(SecurityEvent, "security_events"),
            used_jti=repo(UsedJti, "used_jti"),
            consents=repo(ConsentRecord, "consents"),
            standing=repo(StandingMandate, "standing_mandates"),
            orders=repo(OrderMandate, "order_mandates"),
            parties=repo(PartyConfig, "parties"),
            blobs=repo(BlobHash, "blob_hashes"),
            quarantine=repo(Quarantine, "quarantine"),
            freezes=repo(TenantFreeze, "freezes"),
        )


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float]) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int | None = None) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= min(self.per_minute, limit if limit is not None else self.per_minute):
            return False  # a refused request does not count, so a host recovers within a minute
        hits.append(now)
        return True


class Gateway:
    def __init__(
        self,
        settings: Settings,
        signer: Signer,
        *,
        clock: Callable[[], float] = time.time,
        rate_per_minute: int | None = None,
    ) -> None:
        self.settings = settings
        self.signer = signer
        self.clock = clock
        self.store = Store.create(settings)
        self.audit = AuditLog(self.store.audit)
        self.bus = Broadcaster()
        self.screener = get_screener(settings)
        self.trust_scoring = ConfigLoader(settings.vl_config_dir).trust_scoring()
        self.rate = RateLimiter(rate_per_minute or settings.vl_rate_limit_per_minute, clock)
        self.control = ControlPlane(self)

    # ---- keys -------------------------------------------------------------------------------

    async def public_key_b64(self) -> str:
        return b64e(await self.signer.public_key(GATEWAY_AGENT_ID))

    # ---- audit & containment ----------------------------------------------------------------

    async def config_hash_for(self, retailer_id: str | None) -> str:
        if retailer_id is None:
            return NO_CONFIG
        party = await self.store.parties.get(retailer_id)
        return party.config_hash if party else NO_CONFIG

    async def record(
        self,
        *,
        actor: str,
        on_behalf_of: str,
        action: str,
        target: str,
        payload: Any = None,
        payload_hash: str | None = None,
        outcome: Literal["ok", "denied", "error"] = "ok",
        retailer_id: str | None = None,
        config_hash: str | None = None,
    ) -> AuditRecord:
        record = await self.audit.append(
            actor_agent_id=actor,
            on_behalf_of=on_behalf_of,
            action=action,
            target=target,
            payload_hash=payload_hash or hash_obj(payload if payload is not None else {}),
            config_hash=config_hash or await self.config_hash_for(retailer_id),
            outcome=outcome,
        )
        self.bus.publish("audit", record)
        if outcome != "ok":
            level = logging.WARNING
        elif action.startswith(GATEWAY_EVENTS):
            level = logging.INFO
        else:  # services log their own actions; relays get a summary line
            level = logging.DEBUG
        log.log(
            level,
            f"audit {action}",
            seq=record.seq,
            actor=actor,
            on_behalf_of=on_behalf_of,
            target=target,
            outcome=outcome,
        )
        return record

    async def is_quarantined(self, subject: str) -> bool:
        return await self.store.quarantine.get(subject) is not None

    async def incident(
        self,
        event_type: EventType,
        agent_id: str,
        evidence: str,
        *,
        revoke: bool = False,
        quarantine_subject: str | None = None,
    ) -> SecurityEvent:
        actions = ["blocked"]
        cred = await self.store.credentials.get(agent_id)
        if revoke and cred is not None and cred.status == "active":
            await self._revoke(cred, f"auto: {event_type}")
            actions.append("credential_revoked")
        if quarantine_subject:
            await self.store.quarantine.put(
                quarantine_subject,
                Quarantine(subject=quarantine_subject, reason=event_type, ts=datetime.now(UTC)),
            )
            actions.append(f"quarantined:{quarantine_subject}")
        event = SecurityEvent(
            id=uuid.uuid4().hex,
            ts=datetime.now(UTC),
            type=event_type,
            agent_id=agent_id,
            evidence=evidence[:500],
            action_taken=", ".join(actions),
        )
        await self.store.events.put(event.id, event)
        self.bus.publish("security_event", event)
        log.warning(
            f"security event {event_type}: blocked",
            agent=agent_id,
            action_taken=event.action_taken,
            evidence=event.evidence,
        )
        return event

    async def _revoke(self, cred: AgentCredential, reason: str) -> None:
        revoked = cred.model_copy(update={"status": "revoked", "revoked_at": datetime.now(UTC)})
        await self.store.credentials.put(cred.agent_id, revoked)
        await self.record(
            actor=GATEWAY_AGENT_ID,
            on_behalf_of="platform",
            action="credential.revoke",
            target=cred.agent_id,
            payload={"reason": reason},
        )

    async def handle_denied(self, exc: Denied, *, agent_id: str, target: str, body: bytes) -> None:
        cred = await self.store.credentials.get(agent_id)
        if exc.event is not None:
            evidence = f"{exc.code}: {exc.detail} (path={target}, body_sha256={sha256_hex(body)})"
            await self.incident(exc.event, agent_id, evidence, revoke=exc.revoke)
        self.control.emit(
            "security" if exc.event else "permission_check",
            agent_id,
            exc.code,
            on_behalf_of=cred.on_behalf_of if cred else "",
            target=target,
            outcome="denied",
            attrs={"detail": exc.detail[:200]},
        )
        await self.record(
            actor=agent_id,
            on_behalf_of=cred.on_behalf_of if cred else "unknown",
            action=f"deny.{exc.code}",
            target=target,
            payload_hash=sha256_hex(body),
            outcome="denied",
        )

    # ---- identity ---------------------------------------------------------------------------

    async def register_credential(self, req: CredentialRequest) -> AgentCredential:
        try:
            if len(b64d(req.public_key)) != 32:
                raise ValueError
        except (ValueError, binascii.Error) as exc:
            raise Denied(422, "bad_public_key") from exc
        cred = AgentCredential(
            agent_id=req.agent_id,
            party_type=req.party_type,
            kind=req.kind,
            host=req.agent_id.removeprefix("host:") if req.kind == "host" else None,
            on_behalf_of=req.on_behalf_of,
            display_name=req.display_name,
            public_key=req.public_key,
            key_fingerprint=fingerprint(req.public_key),
            endpoint=req.endpoint,
            issued_at=datetime.now(UTC),
        )
        await self.store.credentials.put(cred.agent_id, cred)
        await self.record(
            actor=GATEWAY_AGENT_ID,
            on_behalf_of=req.on_behalf_of,
            action="credential.issue",
            target=req.agent_id,
            payload=cred,
        )
        return cred

    async def authenticate(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> AgentCredential:
        """Verify a signed request from a registered, active, non-quarantined agent."""
        agent_id = headers.get(HEADER_AGENT, "unknown")
        jti = headers.get(HEADER_JTI, "")
        signature = headers.get(HEADER_SIGNATURE, "")
        cred = await self.store.credentials.get(agent_id)
        if cred is None or not verify_request(cred.public_key, method, path, body, jti, signature):
            raise Denied(401, "bad_signature", "request signature invalid", event="bad_signature")
        await self._require_active(cred)
        if not jti or await self.store.used_jti.get(jti) is not None:
            raise Denied(401, "replay", "request id already used", event="bad_token")
        await self.store.used_jti.put(jti, UsedJti(jti=jti, ts=self.clock()))
        if not self.rate.allow(agent_id, cred.rate_limit_per_min):
            # A party agent flooding the network is revoked. A host only reports telemetry and
            # registers its agents: over the limit it is refused, never revoked, or a burst of
            # events would cut a whole service off until the next reset.
            revoke = cred.kind != "host"
            raise Denied(429, "rate", "rate limit exceeded", event="rate", revoke=revoke)
        await self.control.touch(cred)
        return cred

    async def _require_active(self, cred: AgentCredential, *, as_target: bool = False) -> None:
        """Status is checked on every path: revoked, suspended, quarantined or frozen → refused."""
        if cred.status != "active":
            state: str = cred.status
            raise Denied(403, f"target_{state}" if as_target else state, cred.status_reason)
        if await self.control.effective(cred) == "suspended":
            raise Denied(403, "target_frozen" if as_target else "tenant_frozen", cred.on_behalf_of)
        if await self.is_quarantined(cred.on_behalf_of):
            raise Denied(
                403,
                "target_quarantined" if as_target else "quarantined",
                f"{cred.on_behalf_of} is quarantined",
            )

    async def issue_token(self, cred: AgentCredential, req: TokenRequest) -> TokenResponse:
        rule = SKILL_POLICY.get(req.scope)
        target = await self.store.credentials.get(req.aud)
        if rule is None:
            raise Denied(
                403, "out_of_scope", f"no policy for {req.scope}", event="out_of_scope", revoke=True
            )
        if target is None:
            raise Denied(404, "unknown_target", req.aud)
        if cred.permissions and req.scope not in cred.permissions:
            raise Denied(403, "permission_denied", f"{cred.agent_id} may not {req.scope}")
        await self._require_active(target, as_target=True)
        if (cred.party_type, target.party_type) != (rule.from_party, rule.to_party):
            raise Denied(
                403,
                "out_of_scope",
                f"{cred.party_type}->{target.party_type} may not {req.scope}",
                event="out_of_scope",
                revoke=True,
            )
        now = int(self.clock())
        expires = now + self.settings.token_ttl_seconds
        jti = uuid.uuid4().hex
        claims: dict[str, str | int] = {
            "sub": cred.agent_id,
            "obo": cred.on_behalf_of,
            "scope": req.scope,
            "aud": req.aud,
            "jti": jti,
            "iat": now,
            "iat_ms": int(self.clock() * 1000),  # exact issue time, for session cut-offs
            "exp": expires,
        }
        token = await jwt_encode(self.signer, GATEWAY_AGENT_ID, claims)
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action="token.issue",
            target=req.aud,
            payload={k: v for k, v in claims.items() if k not in ("iat", "iat_ms")},
        )
        self.control.emit(
            "login",
            cred.agent_id,
            req.scope,
            on_behalf_of=cred.on_behalf_of,
            target=req.aud,
            attrs={"jti": jti},
        )
        return TokenResponse(token=token, jti=jti, expires_at=datetime.fromtimestamp(expires, UTC))

    # ---- relay ------------------------------------------------------------------------------

    async def relay(
        self, agent_id: str, skill: str, headers: Mapping[str, str], body: bytes
    ) -> dict[str, Any]:
        path = f"/relay/{agent_id}/{skill}"
        token = headers.get("authorization", "").removeprefix("Bearer ").strip()
        try:
            claims = jwt_decode(
                token, b64d(await self.public_key_b64()), audience=agent_id, now=self.clock()
            )
        except JwtError as exc:
            raise Denied(401, "bad_token", str(exc), event="bad_token") from exc
        if (
            claims.get("scope") != skill
            or claims.get("sub") != headers.get(HEADER_AGENT)
            or claims.get("jti") != headers.get(HEADER_JTI)
        ):
            raise Denied(401, "bad_token", "token does not match request", event="bad_token")

        started = self.clock()
        cred = await self.authenticate("POST", path, headers, body)
        valid_after = cred.sessions_valid_after
        issued_ms = int(claims.get("iat_ms", int(claims.get("iat", 0)) * 1000))
        if valid_after is not None and issued_ms < int(valid_after.timestamp() * 1000):
            raise Denied(401, "session_ended", "token predates the end of the session")
        rule = SKILL_POLICY[skill]  # issue_token only signs scopes that have a rule
        target = await self.store.credentials.get(agent_id)
        if target is None or not target.endpoint:
            raise Denied(404, "unknown_target", agent_id)
        await self._require_active(target, as_target=True)

        try:
            envelope = A2AEnvelope.model_validate_json(body)
            message = rule.body.model_validate(envelope.body)
        except ValidationError as exc:
            raise Denied(422, "bad_body", str(exc.errors(include_input=False))[:300]) from exc
        expected = (cred.agent_id, agent_id, skill, cred.on_behalf_of, claims["jti"])
        actual = (
            envelope.from_agent,
            envelope.to_agent,
            envelope.skill,
            envelope.on_behalf_of,
            envelope.jti,
        )
        sender_party, receiver_party = rule.parties(message)
        if (
            expected != actual
            or (sender_party and sender_party != cred.on_behalf_of)
            or (receiver_party and receiver_party != target.on_behalf_of)
        ):
            raise Denied(
                403,
                "out_of_scope",
                "message names other parties than the token",
                event="out_of_scope",
                revoke=True,
            )
        await self._business_policy(skill, message, cred, target)

        findings = await self.screener.scan_text(body_text(envelope.body))
        if findings == [UNAVAILABLE]:  # fail closed, but an outage is not the sender's attack
            raise Denied(503, "screening_unavailable", "screening could not run; try again")
        if findings:
            raise Denied(
                403, "injection", ",".join(sorted(set(findings))), event="injection", revoke=True
            )

        retailer_id = next(
            (c.on_behalf_of for c in (cred, target) if c.party_type == "retailer"), None
        )
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action=f"relay.{skill}",
            target=agent_id,
            payload=envelope,
            retailer_id=retailer_id,
        )

        forward_path = f"/a2a/{agent_id}/{skill}"
        forward_headers = {
            "content-type": "application/json",
            HEADER_AGENT: GATEWAY_AGENT_ID,
            HEADER_JTI: envelope.jti,
            HEADER_SIGNATURE: await sign_request(
                self.signer, GATEWAY_AGENT_ID, "POST", forward_path, body, envelope.jti
            ),
        }
        wait = self.settings.vl_relay_timeout_seconds  # the target's agents may call models
        async with make_client(target.endpoint, timeout=wait) as client:
            response = await client.post(forward_path, content=body, headers=forward_headers)
        try:
            if response.status_code >= 400:
                raise ValueError(f"target returned {response.status_code}: {response.text[:200]}")
            result = rule.response.model_validate(response.json())
        except ValueError as exc:  # includes ValidationError and JSON errors
            await self.record(
                actor=agent_id,
                on_behalf_of=target.on_behalf_of,
                action=f"relay.{skill}.response",
                target=cred.agent_id,
                payload_hash=sha256_hex(response.content),
                outcome="error",
                retailer_id=retailer_id,
            )
            self.control.emit(
                "call",
                cred.agent_id,
                skill,
                on_behalf_of=cred.on_behalf_of,
                target=agent_id,
                outcome="error",
                latency_ms=(self.clock() - started) * 1000,
            )
            log.error(
                f"relay {skill}: {target.on_behalf_of} answered badly",
                sender=cred.agent_id,
                receiver=agent_id,
                error=str(exc)[:300],
            )
            raise Denied(502, "target_error", str(exc)[:300]) from exc
        await self.control.touch(target)
        latency = (self.clock() - started) * 1000
        self.control.emit(
            "call",
            cred.agent_id,
            skill,
            on_behalf_of=cred.on_behalf_of,
            target=agent_id,
            latency_ms=latency,
        )
        status = getattr(result, "status", None)
        if status is not None and skill.startswith("order."):
            self.control.emit(
                "decision",
                agent_id,
                skill,
                on_behalf_of=target.on_behalf_of,
                target=cred.agent_id,
                attrs={"status": str(getattr(status, "value", status))},
            )
        await self.record(
            actor=agent_id,
            on_behalf_of=target.on_behalf_of,
            action=f"relay.{skill}.response",
            target=cred.agent_id,
            payload=result,
            retailer_id=retailer_id,
        )
        log.info(
            f"relay {skill}: {cred.on_behalf_of} → {target.on_behalf_of}",
            sender=cred.agent_id,
            receiver=agent_id,
            status=str(getattr(status, "value", status)) if status is not None else None,
            ms=round(latency),
        )
        return result.model_dump(mode="json")

    async def _business_policy(
        self, skill: str, message: BaseModel, cred: AgentCredential, target: AgentCredential
    ) -> None:
        """Coarse mandate check. Fine-grained accept/counter stays in the Guardian."""
        if skill == "order.propose":
            mandate = await self.store.standing.get(target.on_behalf_of)
            if mandate is not None and cred.on_behalf_of not in mandate.approved_buyers:
                raise Denied(403, "buyer_not_approved", f"{cred.on_behalf_of} not on mandate")

    # ---- screening --------------------------------------------------------------------------

    async def screen(self, cred: AgentCredential, req: ScreenRequest) -> ScreenResult:
        try:
            data = base64.b64decode(req.data_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise Denied(422, "bad_upload", "data_b64 is not base64") from exc
        digest = sha256_hex(data)
        findings: list[str] = []
        text = ""
        try:
            text = self.screener.extract_text(data, req.content_type, req.filename)
        except Exception:  # unreadable document is itself suspicious; send to review
            findings.append("document:unreadable")
        findings += await self.screener.scan_text(text)
        if req.content_type.startswith("image/"):
            findings += self.screener.scan_image(data)
        seen = await self.store.blobs.get(digest)
        if seen is not None and seen.subject != req.subject:
            findings.append("duplicate:uploaded_by_other_vendor")
        elif seen is None:
            await self.store.blobs.put(digest, BlobHash(sha256=digest, subject=req.subject))

        injected = [f for f in findings if f.startswith("injection:")]
        if injected:
            await self.incident(
                "injection",
                cred.agent_id,
                f"{req.purpose} {req.filename} sha256={digest} findings={','.join(injected)}",
                quarantine_subject=req.subject,
            )
        result = ScreenResult(
            allowed=not injected, sha256=digest, findings=findings, extracted_text_chars=len(text)
        )
        self.control.emit(
            "screening",
            cred.agent_id,
            req.purpose,
            on_behalf_of=req.subject,
            target=req.filename,
            outcome="ok" if result.allowed else "denied",
            attrs={"findings": ",".join(findings)[:200]},
        )
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=req.subject,
            action=f"screen.{req.purpose}",
            target=req.subject,
            payload_hash=digest,
            outcome="ok" if result.allowed else "denied",
        )
        return result

    # ---- consents & mandates ----------------------------------------------------------------

    async def register_consent(self, cred: AgentCredential, consent: ConsentRecord) -> None:
        if cred.party_type != "vendor" or cred.on_behalf_of != consent.vendor_id:
            raise Denied(
                403, "out_of_scope", "consent for another vendor", event="out_of_scope", revoke=True
            )
        await self.store.consents.put(consent.id, consent)
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action="consent.register",
            target=consent.id,
            payload=consent,
        )

    async def _consent_matches(self, ref: str, vendor_id: str, subject_hash: str) -> bool:
        consent = await self.store.consents.get(ref)
        return (
            consent is not None
            and consent.vendor_id == vendor_id
            and consent.subject_hash == subject_hash
        )

    async def put_standing_mandate(self, cred: AgentCredential, mandate: StandingMandate) -> None:
        if cred.party_type != "vendor" or cred.on_behalf_of != mandate.vendor_id:
            raise Denied(
                403, "out_of_scope", "mandate for another vendor", event="out_of_scope", revoke=True
            )
        unsigned = mandate.unsigned()
        if not mandate.signature or not verify_signature(
            b64d(cred.public_key), canonical_json(unsigned), b64d(mandate.signature)
        ):
            raise Denied(422, "bad_mandate_signature")
        if not mandate.consent_ref or not await self._consent_matches(
            mandate.consent_ref, mandate.vendor_id, hash_obj(unsigned)
        ):
            raise Denied(422, "consent_mismatch", "no consent for these exact rules")
        await self.store.standing.put(mandate.vendor_id, mandate)
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action="mandate.standing",
            target=mandate.vendor_id,
            payload=mandate,
        )

    async def put_order_mandate(self, cred: AgentCredential, req: OrderMandateRequest) -> None:
        proposal, mandate = req.proposal, req.mandate
        if cred.party_type != "retailer" or cred.on_behalf_of != proposal.retailer_id:
            raise Denied(
                403, "out_of_scope", "order for another retailer", event="out_of_scope", revoke=True
            )
        terms_hash = hash_obj(proposal)
        if (
            mandate.terms_hash != terms_hash
            or mandate.order_id != proposal.order_id
            or mandate.final_version != proposal.version
        ):
            raise Denied(422, "terms_mismatch", "mandate does not describe this proposal")
        vendors = await self.store.credentials.query(
            on_behalf_of=proposal.vendor_id, party_type="vendor", status="active"
        )
        signed = canonical_json(proposal)
        if not any(
            verify_signature(b64d(v.public_key), signed, b64d(mandate.vendor_sig)) for v in vendors
        ):
            raise Denied(422, "bad_vendor_signature")
        if not verify_signature(b64d(cred.public_key), signed, b64d(mandate.retailer_sig)):
            raise Denied(422, "bad_retailer_signature")
        if mandate.consent_ref and not await self._consent_matches(
            mandate.consent_ref, proposal.vendor_id, terms_hash
        ):
            raise Denied(422, "consent_mismatch", "consent is for different terms")
        await self.store.orders.put(proposal.order_id, mandate)
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action="mandate.order",
            target=proposal.order_id,
            payload=mandate,
            retailer_id=proposal.retailer_id,
        )

    async def set_party_config(self, cred: AgentCredential, config: PartyConfig) -> None:
        if cred.party_type != "retailer" or cred.on_behalf_of != config.retailer_id:
            raise Denied(
                403,
                "out_of_scope",
                "config for another retailer",
                event="out_of_scope",
                revoke=True,
            )
        await self.store.parties.put(config.retailer_id, config)
        await self.record(
            actor=cred.agent_id,
            on_behalf_of=cred.on_behalf_of,
            action="config.activate",
            target=config.retailer_id,
            payload=config,
            config_hash=config.config_hash,
        )

    # ---- reads ------------------------------------------------------------------------------

    async def audit_for(self, party: str | None, limit: int) -> list[AuditRecord]:
        records = await self.audit.records()
        if party:
            agents = {c.agent_id for c in await self.store.credentials.query(on_behalf_of=party)}
            records = [
                r
                for r in records
                if r.on_behalf_of == party or r.target in agents or r.target == party
            ]
        return records[-limit:]
