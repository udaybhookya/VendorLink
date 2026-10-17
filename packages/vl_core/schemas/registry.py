"""Agent cards and the agents config (`config/agents.json`)."""

from __future__ import annotations

import os
import re
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from vl_core.schemas.common import VLModel

AgentKind = Literal["core", "specialist", "channel"]
Tenant = Literal["vendor", "retailer", "any"]
Transport = Literal["local", "a2a"]


class SkillSpec(VLModel):
    capability: str = Field(description="e.g. grade.produce, guardian.evaluate")
    input_schema: str = Field(description="Name of a vl_core.schemas model")
    output_schema: str = Field(description="Name of a vl_core.schemas model")
    description: str = ""
    internal: bool = Field(
        default=False,
        description="Callable in-process only; never exposed by a standalone host or over A2A",
    )


class AgentCard(VLModel):
    """What an agent is and what it can do. Lives next to the agent as `agent.json`."""

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    kind: AgentKind
    tenant: Tenant = Field(description="Which tenant may host it in-process")
    description: str = ""
    version: str = "0.1.0"
    skills: list[SkillSpec] = Field(min_length=1)
    permissions: list[str] = Field(default_factory=list)
    transport: Transport = "local"
    entrypoint: str | None = Field(default=None, description="'module:AgentClass' for local")
    endpoint: str | None = Field(default=None, description="Base URL for a2a")

    @model_validator(mode="after")
    def _transport_target(self) -> Self:
        if self.transport == "local" and not self.entrypoint:
            raise ValueError("local transport needs an entrypoint")
        if self.transport == "a2a" and not self.endpoint:
            raise ValueError("a2a transport needs an endpoint")
        caps = [s.capability for s in self.skills]
        if len(caps) != len(set(caps)):
            raise ValueError("duplicate capability in skills")
        return self

    @property
    def capabilities(self) -> list[str]:
        return [s.capability for s in self.skills]

    def skill(self, capability: str) -> SkillSpec | None:
        return next((s for s in self.skills if s.capability == capability), None)

    def public(self) -> AgentCard:
        """The card a standalone host publishes: internal skills removed."""
        return self.model_copy(update={"skills": [s for s in self.skills if not s.internal]})


_ENV = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def expand_env(value: str) -> str:
    """`${VAR}` or `${VAR:-default}` from the environment (endpoints differ per deployment)."""
    return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)


class AgentEntry(VLModel):
    """One agent registered on the platform."""

    id: str
    card: str = Field(description="Path to agent.json, relative to the repository root")
    enabled: bool = True
    hosts: list[str] = Field(
        default_factory=list,
        description="Deployables that run it in-process, e.g. vendor-svc, retailer-svc",
    )
    transport: Transport | None = Field(default=None, description="Overrides the card")
    endpoint: str | None = Field(default=None, description="Overrides the card; ${ENV} allowed")
    settings: dict[str, Any] = Field(default_factory=dict, description="Agent's own settings")


class AgentsConfig(VLModel):
    version: int = Field(ge=1)
    agents: list[AgentEntry]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [a.id for a in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate agent id in config/agents.json")
        return self


class AgentStatus(VLModel):
    """A registered agent as seen from one host."""

    card: AgentCard
    route: Transport | None = Field(description="local, a2a, or None when unavailable here")
    hosts: list[str]
    enabled: bool
