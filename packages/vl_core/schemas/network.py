"""Suppliers and retailers finding each other: registration, directory, invites, declines."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from vl_core.schemas.common import LocalizedText, VLModel
from vl_core.schemas.config import DocSpec
from vl_core.schemas.jobs import TermsProposal


class VendorRegistration(VLModel):
    display_name: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    business_profile: str
    country: str
    language: str
    retailers: list[str] = Field(default_factory=list)
    email: str | None = None


class DirectoryEntry(VLModel):
    party_id: str
    party_type: Literal["vendor", "retailer"]
    display_name: str
    agent_id: str
    status: str


class VendorInvite(VLModel):
    invite_id: str
    retailer_id: str
    vendor_id: str
    retailer_name: str = ""
    message: str = ""
    sent_at: datetime


class VendorDeclined(VLModel):
    """Vendor → retailer: she said no to this exact version."""

    order_id: str
    version: int
    vendor_id: str
    retailer_id: str
    reason: str = "VENDOR_DECLINED"


class CountryOption(VLModel):
    code: str
    name: LocalizedText
    languages: list[str]
    default_language: str
    currency: str


class BusinessOption(VLModel):
    id: str
    name: LocalizedText
    units: list[str]
    required_docs: list[str]


class VendorOptions(VLModel):
    countries: list[CountryOption]
    business_profiles: list[BusinessOption]
    retailers: list[DirectoryEntry]


class RequiredDocs(VLModel):
    """Which documents this vendor must provide, resolved from her profile + country pack."""

    roles: dict[str, list[DocSpec]]


class VendorTerms(VLModel):
    """Terms a retailer proposed, as the vendor sees them, with her Guardian's verdict."""

    terms: TermsProposal
    verdict: str = Field(description="WITHIN_RULES, PAYMENT_TOO_LATE or NO_STANDING_MANDATE")
    status: Literal["proposed", "accepted", "declined"] = "proposed"
    received_at: datetime
    answered_at: datetime | None = None


class TermsResponse(VLModel):
    """Vendor → retailer (gateway skill terms.response)."""

    terms_id: str
    retailer_id: str
    vendor_id: str
    accepted: bool
    reason: str = ""
    answered_at: datetime
