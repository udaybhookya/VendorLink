"""Inputs and outputs of the core agents' skills (Guardian, Concierge, Orchestrator)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from vl_core.schemas.common import Dec, VLModel
from vl_core.schemas.config import ActiveConfig
from vl_core.schemas.jobs import (
    ListingStepIn,
    ListingStepOut,
    OrderPlanIn,
    OrderPlanOut,
    TermsOut,
    VendorCheckIn,
    VendorCheckOut,
)
from vl_core.schemas.mandate import ConsentRecord, StandingMandate
from vl_core.schemas.order import Decision, OrderProposal
from vl_core.schemas.vendor import Listing

# ---- Guardian -----------------------------------------------------------------------------


class EvaluateIn(VLModel):
    """Pure evaluation: the caller supplies the mandate. Never signs."""

    proposal: OrderProposal
    mandate: StandingMandate | None
    committed: dict[str, Dec] = Field(default_factory=dict, description="sku → qty already signed")


class DecideIn(VLModel):
    """Decide against the vendor's stored mandate; signs when green."""

    proposal: OrderProposal


class SignOrderIn(VLModel):
    proposal: OrderProposal
    consent: ConsentRecord


class SignOrderOut(VLModel):
    vendor_sig: str
    decision: Decision


class SignMandateIn(VLModel):
    mandate: StandingMandate
    consent: ConsentRecord


class MandateQuery(VLModel):
    vendor_id: str


class MandateOut(VLModel):
    mandate: StandingMandate | None


# ---- Concierge ----------------------------------------------------------------------------


class ConciergeChatIn(VLModel):
    vendor_id: str
    text: str
    language: str | None = None


class ConciergePhotoIn(VLModel):
    vendor_id: str
    data_b64: str
    content_type: str = "image/jpeg"


# ---- Orchestrator -------------------------------------------------------------------------


class BuildListingIn(VLModel):
    active_config: ActiveConfig
    step_in: ListingStepIn


class BuildListingOut(VLModel):
    result: ListingStepOut
    ran: list[str] = Field(description="Agent ids, in order")


class OnboardIn(VLModel):
    active_config: ActiveConfig
    check_in: VendorCheckIn


class OnboardOut(VLModel):
    result: VendorCheckOut
    ran: list[str]
    status: Literal["approved", "pending_approval", "checks_failed"]


class PlanOrdersIn(VLModel):
    active_config: ActiveConfig
    plan_in: OrderPlanIn


class PolicyGate(VLModel):
    order_id: str
    auto: bool
    reason: str = ""


class PlanOrdersOut(VLModel):
    plan: OrderPlanOut
    gates: list[PolicyGate]
    ran: list[str]


class CounterReviewIn(VLModel):
    active_config: ActiveConfig
    original: OrderProposal
    counter: OrderProposal


class CounterReviewOut(VLModel):
    accept: bool
    delta: float = Field(description="Relative change in order value")
    reason: str = ""


class TermsJobIn(VLModel):
    active_config: ActiveConfig
    retailer_id: str
    vendor_id: str
    template_id: str | None = None


class ListingReviewIn(VLModel):
    active_config: ActiveConfig
    listing: Listing
    vendor_approved: bool


class ListingReviewOut(VLModel):
    publish: bool
    reasons: list[str] = Field(default_factory=list)


TermsJobOut = TermsOut
