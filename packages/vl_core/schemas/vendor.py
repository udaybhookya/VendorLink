from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import Fraction, Money, Qty, VLModel


class VendorStatus(StrEnum):
    DRAFT = "draft"
    VERIFYING = "verifying"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"


class VendorDoc(VLModel):
    doc_type: str = Field(description="A DocSpec id from the Country Pack")
    number: str | None = None
    uri: str
    status: Literal["submitted", "verified", "failed"] = "submitted"
    failure_reason: str | None = None


class Vendor(VLModel):
    id: str
    display_name: str
    legal_name: str | None = None
    business_profile: str
    country: str
    language: str
    status: VendorStatus = VendorStatus.DRAFT
    docs: list[VendorDoc] = Field(default_factory=list)
    agent_id: str | None = None
    retailers: list[str] = Field(default_factory=list, description="Retailers she works with")
    email: str | None = Field(default=None, description="Optional: for nudges back to the app")
    synthetic: bool = True


AgentState = Literal["active", "suspended", "quarantined", "revoked"]


class AgentCredential(VLModel):
    """The gateway's record of one agent: identity, key, permissions and status.

    Party agents (one per vendor or retailer) hold their own key. Agents hosted in-process by a
    service (Guardian, Orchestrator, specialists) are registered by that host and act under it.
    """

    agent_id: str
    party_type: Literal["vendor", "retailer", "platform"]
    kind: Literal["party", "core", "specialist", "channel", "host"] = "party"
    on_behalf_of: str = Field(description="Vendor or retailer id the agent acts for")
    display_name: str = ""
    host: str | None = Field(default=None, description="Deployable running it in-process")
    public_key: str = Field(description="Raw Ed25519 public key, base64url (host's key if hosted)")
    key_fingerprint: str = ""
    endpoint: str | None = Field(default=None, description="A2A base URL the gateway forwards to")
    permissions: list[str] = Field(
        default_factory=list, description="Allowed skills/capabilities; empty = policy defaults"
    )
    rate_limit_per_min: int = Field(default=120, ge=0)
    status: AgentState = "active"
    status_reason: str = ""
    issued_at: datetime
    revoked_at: datetime | None = None
    last_seen: datetime | None = None
    key_rotation_requested: bool = False
    sessions_valid_after: datetime | None = Field(
        default=None, description="Tokens issued before this are void (end session)"
    )


class ListingFlag(VLModel):
    code: str = Field(description="Machine code, e.g. UNSUPPORTED_CLAIM_ORGANIC")
    detail: str = ""


class Listing(VLModel):
    id: str
    vendor_id: str
    sku_name: str
    category: str
    unit: str
    price: Money
    currency: str
    qty: Qty
    grade: str | None = None
    shelf_life_days: int | None = Field(default=None, ge=0)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    photos: list[str] = Field(default_factory=list)
    flags: list[ListingFlag] = Field(default_factory=list)
    confidence: Fraction = 0.0
    status: Literal["draft", "needs_review", "published", "rejected"] = "draft"
