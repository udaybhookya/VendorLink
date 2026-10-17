"""Agent control plane: one event format, control actions, hosted-agent registration, and the
observability views built from them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import VLModel
from vl_core.schemas.vendor import AgentCredential, AgentState

EventType = Literal[
    "call",
    "decision",
    "login",
    "permission_check",
    "screening",
    "control",
    "security",
    "audit",
    "ask",
    "answer",
    "notify",
]
Scalar = str | int | float | bool


class AgentEvent(VLModel):
    """Every operational fact in one shape: calls, decisions, logins, permission checks,
    screening and control actions. The audit log stays the formal, tamper-evident record."""

    id: str
    ts: datetime
    type: EventType
    agent_id: str
    on_behalf_of: str = ""
    action: str = Field(description="Capability, skill, audit action or control verb")
    target: str = ""
    outcome: Literal["ok", "denied", "error"] = "ok"
    latency_ms: float | None = None
    trace_id: str = ""
    span_id: str = ""
    actor: str | None = Field(default=None, description="Person, for control actions")
    attrs: dict[str, Scalar] = Field(default_factory=dict)


class EventBatch(VLModel):
    events: list[AgentEvent]


class HostedAgent(VLModel):
    agent_id: str
    kind: Literal["core", "specialist", "channel"]
    tenant: Literal["vendor", "retailer"]
    on_behalf_of: str
    permissions: list[str] = Field(default_factory=list, description="Its skills")
    display_name: str = ""


class HostedAgentsRegistration(VLModel):
    host: str
    agents: list[HostedAgent]


ControlAction = Literal[
    "suspend",
    "resume",
    "quarantine",
    "revoke",
    "rotate_key",
    "set_permissions",
    "set_rate_limit",
    "end_sessions",
]


class ControlRequest(VLModel):
    actor: str = Field(min_length=1, description="The person taking the action")
    reason: str = Field(min_length=1)
    permissions: list[str] | None = None
    rate_limit_per_min: int | None = Field(default=None, ge=0)


class TenantFreeze(VLModel):
    tenant: str = Field(description="vendor, retailer, or one party id")
    frozen: bool
    actor: str
    reason: str
    ts: datetime


class KeyRotation(VLModel):
    public_key: str


class AgentStatusEntry(VLModel):
    """What a host's registry needs to gate in-process calls."""

    agent_id: str
    status: AgentState
    effective: AgentState = Field(description="Status after tenant freezes are applied")
    permissions: list[str]
    rate_limit_per_min: int
    key_rotation_requested: bool


class AgentStatusSnapshot(VLModel):
    agents: list[AgentStatusEntry]
    frozen: list[str]


class AgentMetrics(VLModel):
    calls: int = 0
    errors: int = 0
    denied: int = 0
    success_rate: float = 1.0
    p50_ms: float | None = None
    p95_ms: float | None = None
    calls_last_5m: int = 0
    needs_review_rate: float | None = None
    decisions: dict[str, int] = Field(default_factory=dict)


class TrustScoring(VLModel):
    """Weights of the trust score (0–100, from 100). `config/scoring/trust.json`."""

    note: str = ""
    suspended: int = Field(default=40, ge=0, le=100, description="Points off while suspended")
    quarantined: int = Field(default=60, ge=0, le=100, description="Points off while quarantined")
    per_violation: int = Field(default=25, ge=0, description="Per verified violation by the agent")
    max_violations: int = Field(default=75, ge=0, le=100)
    per_impersonation: int = Field(default=2, ge=0, description="Per blocked call in its name")
    max_impersonation: int = Field(default=10, ge=0, le=100)
    error_rate: int = Field(default=30, ge=0, le=100, description="× share of calls that failed")
    denial_rate: int = Field(default=20, ge=0, le=100, description="× share of calls denied")
    needs_review_rate: int = Field(default=20, ge=0, le=100, description="× needs-review rate")


class TrustFactor(VLModel):
    name: str
    impact: float = Field(description="Points added (+) or removed (-) from 100")
    detail: str = ""


class AgentObservation(VLModel):
    agent: AgentCredential
    effective_status: AgentState
    live: bool
    metrics: AgentMetrics
    trust_score: int = Field(ge=0, le=100)
    trust_factors: list[TrustFactor]
    quality_score: int | None = Field(default=None, ge=0, le=100)


class TraceSummary(VLModel):
    trace_id: str
    started: datetime
    duration_ms: float
    agents: list[str]
    actions: list[str]
    outcome: Literal["ok", "denied", "error"]
    events: int


class TraceView(VLModel):
    summary: TraceSummary
    events: list[AgentEvent]


class SafetySummary(VLModel):
    security_events: dict[str, int]
    screening_blocked: int
    quarantined_subjects: list[str]
    frozen: list[str]
    agents_by_status: dict[str, int]


class QualityRow(VLModel):
    agent_id: str
    calls: int
    needs_review_rate: float | None
    error_rate: float
    top_flags: dict[str, int]
    quality_score: int | None


class ControlResult(VLModel):
    agent: AgentCredential | None = None
    frozen: list[str] = Field(default_factory=list)
    detail: str = ""


class HumanLoopSummary(VLModel):
    """How agents' questions to people are going (from ask / answer events)."""

    open: int
    answered: int
    expired: int
    median_minutes_to_answer: float | None
    by_channel: dict[str, int]
    by_role: dict[str, int]
    recent: list[AgentEvent]
