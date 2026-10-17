"""Request and response bodies of the trust gateway API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import VLModel
from vl_core.schemas.mandate import OrderMandate
from vl_core.schemas.order import OrderProposal


class Ack(VLModel):
    ok: bool = True
    detail: str = ""


class GatewayErrorBody(VLModel):
    error: str = Field(description="Machine code, e.g. bad_signature, out_of_scope")
    detail: str = ""


class CredentialRequest(VLModel):
    agent_id: str
    party_type: Literal["vendor", "retailer", "platform"]
    on_behalf_of: str
    public_key: str
    endpoint: str | None = None
    display_name: str = ""
    kind: Literal["party", "host"] = "party"


class TokenRequest(VLModel):
    scope: str = Field(description="Exactly one skill, e.g. order.propose")
    aud: str = Field(description="Target agent id")


class TokenResponse(VLModel):
    token: str
    jti: str
    expires_at: datetime


class AuditAppend(VLModel):
    """A state change reported by a service. Actor and on_behalf_of come from the signature."""

    action: str
    target: str
    payload_hash: str
    outcome: Literal["ok", "denied", "error"] = "ok"
    retailer_id: str | None = Field(default=None, description="Selects the config_hash recorded")


class AuditVerifyResult(VLModel):
    ok: bool
    checked: int
    first_broken_seq: int | None = None


class ScreenRequest(VLModel):
    filename: str
    content_type: str
    data_b64: str
    purpose: str = Field(description="e.g. vendor_document, listing_photo")
    subject: str = Field(description="Vendor id the upload belongs to")


class ScreenResult(VLModel):
    allowed: bool
    sha256: str
    findings: list[str] = Field(default_factory=list)
    extracted_text_chars: int = 0


class PartyConfig(VLModel):
    retailer_id: str
    config_hash: str
    preset: str | None = None


class OrderMandateRequest(VLModel):
    proposal: OrderProposal
    mandate: OrderMandate


class RevokeRequest(VLModel):
    reason: str
