from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import VLModel


class AuditRecord(VLModel):
    """One link in the append-only, hash-chained audit log."""

    seq: int = Field(ge=0)
    ts: datetime
    actor_agent_id: str
    on_behalf_of: str
    action: str
    target: str
    payload_hash: str
    config_hash: str
    outcome: Literal["ok", "denied", "error"]
    prev_hash: str
    hash: str


class SecurityEvent(VLModel):
    id: str
    ts: datetime
    type: Literal["bad_token", "bad_signature", "out_of_scope", "injection", "rate"]
    agent_id: str
    session_id: str | None = None
    evidence: str = Field(description="Summary or hash only; never raw documents")
    action_taken: str
