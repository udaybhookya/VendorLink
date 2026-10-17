"""The agent registry: which agents exist, where they run, and how to call them.

Agents are registered in `config/agents.json`. Each entry points at the agent's card
(`agent.json`) and says which deployables run it in-process (`hosts`). Anywhere else the agent is
reached over A2A at its `endpoint`, or is unavailable. Adding an agent never changes this file.

    registry = AgentRegistry.load(settings, host="retailer-svc", signer=signer)
    card = registry.find("grade.*", active_config)        # by capability, never by import
    out = await registry.invoke(card, "grade.*", step_in)  # local or A2A, same call
"""

from __future__ import annotations

import importlib
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from agents.base import Agent, AgentContext, AgentError
from agents.control import HostControl
from vl_core import schemas
from vl_core.a2a import make_client
from vl_core.config import ConfigError, ConfigLoader
from vl_core.crypto import Signer
from vl_core.llm.usage import usage_scope
from vl_core.obs import get_logger, party_var
from vl_core.obs.tracing import agent_span, current_ids
from vl_core.registry import RegistryError, capability_matches
from vl_core.schemas import (
    ActiveConfig,
    AgentCard,
    AgentEntry,
    AgentEvent,
    HumanTask,
    expand_env,
)
from vl_core.schemas.control import Scalar
from vl_core.settings import Settings, get_settings

log = get_logger("agents")
# Routine reads (the background loop, the screens' polling): logged at DEBUG, not INFO.
ROUTINE_CAPABILITIES = ("human.tick", "human.list", "channel.*.poll", "guardian.mandate")

HOST_TENANT = {"vendor-svc": "vendor", "retailer-svc": "retailer"}
STANDALONE = "standalone"


class AgentBlocked(RegistryError):
    """The control plane has suspended, quarantined, revoked, frozen or rate-limited the agent."""

    def __init__(self, agent_id: str, reason: str) -> None:
        super().__init__(f"{agent_id} blocked: {reason}")
        self.agent_id = agent_id
        self.reason = reason


def event_attrs(result: BaseModel) -> tuple[str, dict[str, Scalar]]:
    """Event type and the facts worth tracking from a skill's output (no payload content)."""
    attrs: dict[str, Scalar] = {}
    inner = getattr(result, "result", None) or result
    if hasattr(inner, "needs_review"):
        attrs["needs_review"] = bool(inner.needs_review)
    flags = getattr(inner, "flags", None)
    if flags:
        attrs["flags"] = ",".join(sorted({f.code for f in flags}))[:300]
    if hasattr(inner, "passed"):
        attrs["passed"] = bool(inner.passed)
    decision = getattr(result, "decision", None) or result
    status = getattr(decision, "status", None)
    if status is not None:
        attrs["status"] = str(getattr(status, "value", status))
    reasons = getattr(decision, "reasons", None)
    if reasons:
        attrs["reasons"] = ",".join(str(getattr(r, "code", r)) for r in reasons)[:300]
    if getattr(decision, "vendor_sig", None):
        attrs["signed"] = True
    kind = "decision" if "status" in attrs and hasattr(decision, "reasons") else "call"
    if isinstance(result, HumanTask):
        attrs.update(
            {
                "task": result.id,
                "role": result.role,
                "task_kind": result.kind,
                "channels": ",".join(sorted({d.channel for d in result.deliveries})),
            }
        )
        kind = "ask" if result.status == "open" else "answer"
        if result.answered_via:
            attrs["via"] = result.answered_via
    return kind, attrs


@dataclass
class RegisteredAgent:
    entry: AgentEntry
    card: AgentCard  # declared card, with endpoint override applied
    route: Literal["local", "a2a"] | None  # how *this* host reaches it; None = unavailable

    @property
    def available(self) -> bool:
        return self.route is not None


def schema_model(name: str) -> type[BaseModel]:
    model = getattr(schemas, name, None)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise RegistryError(f"{name!r} is not a vl_core.schemas model")
    return model


def agent_class(card: AgentCard) -> type[Agent]:
    if not card.entrypoint:
        raise RegistryError(f"{card.agent_id} has no entrypoint")
    module_name, _, attr = card.entrypoint.partition(":")
    cls = getattr(importlib.import_module(module_name), attr, None)
    if not (isinstance(cls, type) and issubclass(cls, Agent)):
        raise RegistryError(f"{card.entrypoint} is not an Agent subclass")
    return cls


class AgentRegistry:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        host: str = STANDALONE,
        signer: Signer | None = None,
        resources: dict[str, Any] | None = None,
        loader: ConfigLoader | None = None,
        control: HostControl | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.host = host
        self.control = control
        if control is not None:
            control.registry = self
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self.loader = loader or ConfigLoader(self.settings.vl_config_dir)
        self._signer = signer
        self.resources = dict(resources or {})
        self._agents: dict[str, RegisteredAgent] = {}
        self._instances: dict[str, Agent] = {}

    # ---- loading -------------------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        settings: Settings | None = None,
        *,
        host: str = STANDALONE,
        signer: Signer | None = None,
        resources: dict[str, Any] | None = None,
        control: HostControl | None = None,
    ) -> AgentRegistry:
        registry = cls(settings, host=host, signer=signer, resources=resources, control=control)
        for entry in registry.loader.load_agents_config().agents:
            if entry.enabled:
                registry.register(entry)
        return registry

    def register(self, entry: AgentEntry, card: AgentCard | None = None) -> RegisteredAgent:
        """Add one agent: from config, or programmatically (e.g. an external A2A agent)."""
        card = card or self.loader.load_card(entry)
        endpoint = expand_env(entry.endpoint or card.endpoint or "") or None
        card = card.model_copy(update={"endpoint": endpoint})
        in_process = entry.transport != "a2a" and (
            self.host == STANDALONE or self.host in entry.hosts
        )
        if in_process and card.entrypoint is None:
            in_process = False  # external agent: no code here
        if in_process and self.host != STANDALONE:
            tenant = HOST_TENANT.get(self.host)
            if card.tenant not in ("any", tenant):
                raise RegistryError(
                    f"{card.agent_id} is a {card.tenant}-tenant agent; {self.host} may not host it"
                )
        route: Literal["local", "a2a"] | None = (
            "local" if in_process else ("a2a" if endpoint else None)
        )
        registered = RegisteredAgent(entry=entry, card=card, route=route)
        self._agents[card.agent_id] = registered
        self._instances.pop(card.agent_id, None)
        return registered

    def gateway_id(self, card: AgentCard) -> str:
        """The id the control plane knows this agent by. Agents any tenant may host (Liaison,
        channels) run once per host, so each instance gets its own record: liaison@vendor-svc."""
        if card.tenant == "any" and self.host != STANDALONE:
            return f"{card.agent_id}@{self.host}"
        return card.agent_id

    @property
    def tenant(self) -> Literal["vendor", "retailer"]:
        return "vendor" if HOST_TENANT.get(self.host) == "vendor" else "retailer"

    # ---- lookup ---------------------------------------------------------------------------------

    def registered(self) -> list[RegisteredAgent]:
        return list(self._agents.values())

    @property
    def cards(self) -> list[AgentCard]:
        return [r.card for r in self._agents.values()]

    def card(self, agent_id: str) -> AgentCard:
        return self._get(agent_id).card

    def _get(self, agent_id: str) -> RegisteredAgent:
        registered = self._agents.get(agent_id)
        if registered is None:
            raise RegistryError(f"unknown agent {agent_id!r}")
        return registered

    def find_all(
        self, capability: str, active_config: ActiveConfig | None = None
    ) -> list[AgentCard]:
        """Available agents offering `capability`. Specialists must be loaded by `active_config`."""
        found = []
        for r in self._agents.values():
            if not r.available:
                continue
            if (
                r.card.kind == "specialist"
                and active_config is not None
                and r.card.agent_id not in active_config.specialists
            ):
                continue
            if any(capability_matches(capability, c) for c in r.card.capabilities):
                found.append(r.card)
        return found

    def find(self, capability: str, active_config: ActiveConfig | None = None) -> AgentCard:
        found = self.find_all(capability, active_config)
        if len(found) != 1:
            names = [c.agent_id for c in found]
            raise RegistryError(f"{capability!r} resolves to {len(found)} agents: {names}")
        return found[0]

    # ---- instances & invocation -----------------------------------------------------------------

    def agent(self, agent_id: str) -> Agent:
        """The in-process instance (created once). Hosts use this to run an agent locally."""
        instance = self._instances.get(agent_id)
        if instance is not None:
            return instance
        registered = self._get(agent_id)
        card = registered.card
        cls = agent_class(card)
        try:
            options = cls.Options.model_validate(registered.entry.settings)
        except ValidationError as exc:
            raise RegistryError(f"{agent_id}: bad settings in agents.json: {exc}") from exc
        tenant = HOST_TENANT.get(self.host, card.tenant if card.tenant != "any" else "shared")
        ctx = AgentContext(
            settings=self.settings,
            agent_id=agent_id,
            tenant=tenant,
            registry=self,
            signer=self._signer if "sign" in card.permissions else None,
            resources=self.resources,
        )
        instance = cls(card, ctx, options)
        self._instances[agent_id] = instance
        return instance

    def _capability(self, card: AgentCard, capability: str) -> str:
        matches = [c for c in card.capabilities if capability_matches(capability, c)]
        if len(matches) != 1:
            raise RegistryError(f"{capability!r} matches {matches} on {card.agent_id}")
        return matches[0]

    async def invoke(
        self, target: str | AgentCard, capability: str, payload: BaseModel | dict[str, Any]
    ) -> BaseModel:
        """Call one skill of one agent, in-process or over A2A. Output is schema-validated."""
        registered = self._get(target if isinstance(target, str) else target.agent_id)
        card = registered.card
        concrete = self._capability(card, capability)
        spec = card.skill(concrete)
        assert spec is not None
        if registered.route == "local":
            return await self._run_local(card, concrete, payload)
        if registered.route is None:
            raise RegistryError(f"{card.agent_id} is not available on {self.host}")
        if spec.internal:
            raise RegistryError(f"{concrete} is internal to {card.agent_id}; not callable over A2A")
        body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        started = time.perf_counter()
        async with make_client(card.endpoint or "", timeout=60) as client:
            response = await client.post(f"/invoke/{concrete}", json=body)
        log.info(
            f"{card.agent_id} {concrete} (a2a)",
            endpoint=card.endpoint,
            status=response.status_code,
            ms=round((time.perf_counter() - started) * 1000),
        )
        if response.status_code >= 400:
            raise RegistryError(
                f"{card.agent_id} {concrete} → {response.status_code}: {response.text[:300]}"
            )
        return schema_model(spec.output_schema).model_validate(response.json())

    async def _gate(self, card: AgentCard, capability: str) -> None:
        """Status checked on every in-process call, from the control plane's cached snapshot."""
        if self.control is None:
            return
        gid = self.gateway_id(card)
        entry = await self.control.status(gid)
        if entry is None:
            return
        if entry.effective != "active":
            raise AgentBlocked(gid, entry.effective)
        if entry.permissions and capability not in entry.permissions:
            raise AgentBlocked(gid, f"no permission for {capability}")
        now = time.monotonic()
        calls = self._calls[gid]
        while calls and calls[0] < now - 60:
            calls.popleft()
        calls.append(now)
        if entry.rate_limit_per_min and len(calls) > entry.rate_limit_per_min:
            raise AgentBlocked(gid, "rate limit")

    async def _report(
        self,
        card: AgentCard,
        capability: str,
        *,
        kind: str,
        outcome: str,
        latency_ms: float | None,
        attrs: dict[str, Scalar],
    ) -> None:
        if self.control is None:
            return
        import uuid
        from datetime import UTC, datetime

        trace_id, span_id = current_ids()
        await self.control.report(
            AgentEvent.model_validate(
                {
                    "id": uuid.uuid4().hex,
                    "ts": datetime.now(UTC),
                    "type": kind,
                    "agent_id": self.gateway_id(card),
                    "on_behalf_of": party_var.get() or "",
                    "action": capability,
                    "outcome": outcome,
                    "latency_ms": latency_ms,
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "attrs": attrs,
                }
            )
        )

    async def _run_local(
        self, card: AgentCard, capability: str, payload: BaseModel | dict[str, Any]
    ) -> BaseModel:
        party = party_var.get() or ""
        with agent_span(
            f"{card.agent_id} {capability}",
            agent_id=card.agent_id,
            on_behalf_of=party,
            capability=capability,
            kind=card.kind,
        ):
            try:
                await self._gate(card, capability)
            except AgentBlocked as exc:
                log.warning(f"{card.agent_id} {capability} blocked", reason=exc.reason)
                await self._report(
                    card,
                    capability,
                    kind="permission_check",
                    outcome="denied",
                    latency_ms=None,
                    attrs={"reason": exc.reason},
                )
                raise
            started = time.perf_counter()
            try:
                with usage_scope() as usage:  # tokens of this agent's own model calls
                    result = await self.agent(card.agent_id).invoke(capability, payload)
            except Exception as exc:
                log.warning(
                    f"{card.agent_id} {capability} failed",
                    ms=round((time.perf_counter() - started) * 1000),
                    error=repr(exc)[:300],
                )
                await self._report(
                    card,
                    capability,
                    kind="call",
                    outcome="error",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attrs={"error": type(exc).__name__, **usage.attrs()},
                )
                raise
            kind, attrs = event_attrs(result)
            attrs = {**attrs, **usage.attrs()}
            routine = any(fnmatchcase(capability, p) for p in ROUTINE_CAPABILITIES)
            log.log(
                logging.DEBUG if routine else logging.INFO,
                f"{card.agent_id} {capability}",
                ms=round((time.perf_counter() - started) * 1000),
                fields=attrs,
            )
            await self._report(
                card,
                capability,
                kind=kind,
                outcome="ok",
                latency_ms=(time.perf_counter() - started) * 1000,
                attrs=attrs,
            )
            return result

    async def call[T: BaseModel](
        self,
        capability: str,
        payload: BaseModel | dict[str, Any],
        output: type[T],
        active_config: ActiveConfig | None = None,
    ) -> T:
        """find + invoke + type check: the usual way for hosts to use an agent."""
        result = await self.invoke(self.find(capability, active_config), capability, payload)
        if not isinstance(result, output):
            raise RegistryError(
                f"{capability} returned {type(result).__name__}, not {output.__name__}"
            )
        return result

    # ---- validation ------------------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check every registered agent: schemas exist, code matches card, settings are valid."""
        problems: list[str] = []
        for r in self._agents.values():
            card = r.card
            for spec in card.skills:
                for name in (spec.input_schema, spec.output_schema):
                    try:
                        schema_model(name)
                    except RegistryError as exc:
                        problems.append(f"{card.agent_id}: {exc}")
            if card.entrypoint is None:
                if not card.endpoint:
                    problems.append(f"{card.agent_id}: external agent without endpoint")
                continue
            try:
                cls = agent_class(card)
                problems += [f"{card.agent_id}: {p}" for p in cls.contract_problems(card)]
                cls.Options.model_validate(r.entry.settings)
            except (RegistryError, AgentError, ValidationError, ImportError) as exc:
                problems.append(f"{card.agent_id}: {exc}")
            for host in r.entry.hosts:
                tenant = HOST_TENANT.get(host)
                if tenant is None:
                    problems.append(f"{card.agent_id}: unknown host {host!r}")
                elif card.tenant not in ("any", tenant):
                    problems.append(f"{card.agent_id}: {card.tenant} agent listed on {host}")
        return problems


__all__ = [
    "AgentBlocked",
    "AgentRegistry",
    "ConfigError",
    "RegisteredAgent",
    "RegistryError",
    "agent_class",
]
