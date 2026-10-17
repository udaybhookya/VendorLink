"""Specialist inputs and outputs. Specialists of one capability family share one contract."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import Fraction, VLModel
from vl_core.schemas.config import BusinessProfile, CountryPack, RetailerProfile
from vl_core.schemas.hitl import InfoRequest
from vl_core.schemas.order import OrderProposal
from vl_core.schemas.vendor import Listing, ListingFlag, ListingRules, Vendor


class ListingStepIn(VLModel):
    """Input to every build_listing step (catalogue.build, grade.*, compliance.check)."""

    vendor_id: str
    language: str
    photo_uri: str | None = None
    transcript: str = Field(default="", description="What the vendor said about the item")
    label_text: str | None = Field(default=None, description="Label text if already extracted")
    answers: dict[str, str] = Field(default_factory=dict, description="Vendor's answers so far")
    listing: Listing | None = Field(default=None, description="Draft from the previous step")
    business_profile: BusinessProfile
    retailer_profile: RetailerProfile
    country_pack: CountryPack


class ListingStepOut(VLModel):
    listing: Listing | None
    flags: list[ListingFlag] = Field(default_factory=list)
    needs_review: bool = False
    notes: str = ""
    requests: list[InfoRequest] = Field(
        default_factory=list, description="What a person must tell us before this can continue"
    )


class ListingApprovalIn(VLModel):
    """guardian.review_listing: may her agent approve this improved listing on her behalf?"""

    listing: Listing
    transcript: str = Field(default="", description="What she said about the item")
    answers: dict[str, str] = Field(default_factory=dict, description="Her answers to questions")
    rules: ListingRules = Field(default_factory=ListingRules)


class ListingApprovalOut(VLModel):
    auto_approve: bool
    image: str = Field(default="original", description="The photo to confirm with")
    reasons: list[str] = Field(
        default_factory=list, description="Machine codes: why she must look first"
    )


class DocCheck(VLModel):
    doc_type: str
    status: Literal["pass", "fail", "needs_review"]
    code: str = Field(description="Machine code, e.g. NAME_MISMATCH, LICENCE_EXPIRED")
    detail: str = ""


class VendorCheckIn(VLModel):
    """Input to onboard_vendor steps (vendor.verify, compliance.check)."""

    vendor: Vendor
    legal_name: str
    doc_numbers: dict[str, str] = Field(description="DocSpec id → number")
    business_profile: BusinessProfile
    country_pack: CountryPack
    listings: list[Listing] = Field(default_factory=list)


class VendorCheckOut(VLModel):
    checks: list[DocCheck] = Field(default_factory=list)
    flags: list[ListingFlag] = Field(default_factory=list)
    passed: bool
    requests: list[InfoRequest] = Field(default_factory=list)


class OrderPlanIn(VLModel):
    """Input to order.replenish / order.create."""

    retailer_id: str
    vendor_id: str
    store_id: str
    for_date: date
    listings: list[Listing]
    country_pack: CountryPack
    terms_version: str
    payment_days: int = Field(ge=0)
    demand_override: list[DemandLine] | None = None


class DemandLine(VLModel):
    sku: str
    qty: int = Field(gt=0)
    unit: str
    pickup_local: str = Field(pattern=r"^\d{2}:\d{2}$")


class OrderPlanOut(VLModel):
    proposals: list[OrderProposal]
    notes: str = ""
    unmatched: list[DemandLine] = Field(
        default_factory=list, description="Demand the vendor has no published listing for"
    )


class TermsIn(VLModel):
    retailer_id: str
    vendor_id: str
    retailer_profile: RetailerProfile
    template_id: str | None = None


class TermsProposal(VLModel):
    terms_id: str
    retailer_id: str
    vendor_id: str
    template_id: str
    payment_days: int = Field(ge=0)
    description: dict[str, str] = Field(default_factory=dict)


class TermsOut(VLModel):
    terms: TermsProposal


class GradeResult(VLModel):
    """What a vision model returns for a produce photo."""

    grade: str
    freshness: Literal["fresh", "ok", "wilting", "spoiled"]
    shelf_life_days: int = Field(ge=0)
    confidence: Fraction


class CatalogueDraft(VLModel):
    """What a vision + language model returns for a photo and the vendor's words."""

    sku_name: str
    category: str
    unit: str
    price: str | None = None
    qty: str | None = None
    claims: list[str] = Field(default_factory=list, description="e.g. organic, homemade")
    confidence: Fraction


class LabelFields(VLModel):
    """What a vision model reads off a packaged-food label."""

    product_name: str | None = None
    ingredients: str | None = None
    allergens: str | None = None
    expiry_date: str | None = None
    net_weight: str | None = None
    registration_numbers: dict[str, str] = Field(
        default_factory=dict, description="Label wording → number, e.g. {'P-IRT': '...'}"
    )
    halal_logo: bool = False
    confidence: Fraction


OrderPlanIn.model_rebuild()
