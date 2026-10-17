"""Messages for onboarding, listings, rules and consent, plus the views the web apps read."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from vl_core.schemas.common import Money, Qty, VLModel
from vl_core.schemas.config import ConfigRefs
from vl_core.schemas.jobs import DocCheck, ListingStepOut
from vl_core.schemas.mandate import StandingMandate, TimeWindow
from vl_core.schemas.order import Deal
from vl_core.schemas.vendor import Vendor, VendorStatus


class ListingSubmission(VLModel):
    """Vendor → retailer: a photo and the vendor's words; the retailer builds the draft."""

    vendor_id: str
    language: str
    transcript: str = ""
    photo_b64: str | None = Field(default=None, description="JPEG/PNG, downscaled to ≤1024 px")
    photo_mime: str = "image/jpeg"
    label_text: str | None = None
    answers: dict[str, str] = Field(
        default_factory=dict, description="Answers the vendor gave to earlier questions (price …)"
    )


class VendorApplication(VLModel):
    vendor: Vendor
    legal_name: str
    doc_numbers: dict[str, str] = Field(description="DocSpec id → number")


class VendorStatusUpdate(VLModel):
    vendor_id: str
    status: VendorStatus
    checks: list[DocCheck] = Field(default_factory=list)
    note: str = ""


class SkuRuleDraft(VLModel):
    sku: str
    unit: str
    price_floor: Money
    max_qty_per_day: Qty


class RulesDraft(VLModel):
    """What the vendor said her rules are, before read-back and consent."""

    skus: list[SkuRuleDraft] = Field(min_length=1)
    pickup_windows: list[TimeWindow] = Field(min_length=1)
    payment_days_max: int = Field(ge=0)
    approved_buyers: list[str] = Field(default_factory=list)
    auto_accept_max_value: Money


class ConsentRequest(VLModel):
    """The vendor's spoken or tapped 'yes' to an exact subject she was read back."""

    subject_hash: str
    transcript: str
    language: str
    audio_b64: str | None = None
    audio_mime: str = "audio/webm"


class DealView(VLModel):
    deal: Deal
    subject_hash: str = Field(description="Hash of the latest version; echo it back to consent")
    readback: str


class UiEvent(VLModel):
    type: Literal["deal", "rules", "listing", "consent_needed", "document", "status", "notice"]
    data: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(VLModel):
    text: str
    language: str | None = None


class ChatReply(VLModel):
    reply: str
    language: str
    events: list[UiEvent] = Field(default_factory=list)
    mode: Literal["llm", "offline"]


RetailerVendorStatus = Literal[
    "applied", "checks_failed", "pending_approval", "approved", "rejected"
]


class RetailerVendor(VLModel):
    vendor_id: str
    display_name: str
    country: str
    business_profile: str
    status: RetailerVendorStatus
    checks: list[DocCheck] = Field(default_factory=list)
    updated_at: datetime


class QueueItem(VLModel):
    id: str
    retailer_id: str
    kind: Literal["place_order", "handle_counter", "listing", "vendor"]
    ref_id: str
    reason: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    task_id: str | None = Field(default=None, description="The human task asking someone")
    created_at: datetime


class DocumentUpload(VLModel):
    doc_type: str = Field(description="DocSpec id from the Country Pack")
    number: str | None = None
    data_b64: str | None = None
    content_type: str = "image/jpeg"
    filename: str = "document"


class ActivityItem(VLModel):
    ts: datetime
    action: str
    text: str
    outcome: str
    actor: str


class RulesView(VLModel):
    mandate: StandingMandate | None = None
    draft: StandingMandate | None = None
    readback: str = ""
    subject_hash: str | None = Field(default=None, description="Of the draft awaiting consent")


class ListingDraftView(VLModel):
    result: ListingStepOut
    readback: str


class PlaceOrderRequest(VLModel):
    vendor_id: str
    store_id: str
    for_date: date


class ActivateRequest(VLModel):
    preset: str | None = None
    refs: ConfigRefs | None = None


class Store(VLModel):
    id: str
    name: str


class Retailer(VLModel):
    id: str
    display_name: str
    stores: list[Store] = Field(default_factory=list)
    synthetic: bool = True


class ProfileUpdate(VLModel):
    display_name: str | None = None
    legal_name: str | None = None
    language: str | None = Field(
        default=None,
        description="Her preferred language: the app, read-backs and questions follow it",
    )
