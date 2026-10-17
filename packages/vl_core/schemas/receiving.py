from __future__ import annotations

from datetime import datetime

from pydantic import Field

from vl_core.schemas.common import Dec, VLModel


class ReceivingReport(VLModel):
    order_id: str
    received_qty: Dec = Field(ge=0)
    rejected_qty: Dec = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    received_at: datetime


class DisputeEvent(VLModel):
    ts: datetime
    actor: str
    note: str


class Dispute(VLModel):
    order_id: str
    received_qty: Dec = Field(ge=0)
    rejected_qty: Dec = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    timeline: list[DisputeEvent] = Field(default_factory=list)
