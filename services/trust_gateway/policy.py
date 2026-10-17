"""Deny-by-default skill policy. A new skill needs an entry here and a negative test."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from vl_core.schemas import (
    Ack,
    CounterResponse,
    Decision,
    InfoRequestMessage,
    InfoResponseMessage,
    Listing,
    ListingStatusUpdate,
    ListingStepOut,
    ListingSubmission,
    OrderProposal,
    SignedOrder,
    TermsProposal,
    TermsResponse,
    VendorApplication,
    VendorCheckOut,
    VendorDeclined,
    VendorInvite,
    VendorStatusUpdate,
)

PartyType = Literal["vendor", "retailer", "platform"]


@dataclass(frozen=True)
class SkillRule:
    from_party: PartyType
    to_party: PartyType
    body: type[BaseModel]
    response: type[BaseModel]
    parties: Callable[[BaseModel], tuple[str, str]]
    """(sender on_behalf_of, receiver on_behalf_of) the body claims; must match the credentials."""


def _order(m: BaseModel) -> tuple[str, str]:
    assert isinstance(m, OrderProposal)
    return m.retailer_id, m.vendor_id


def _counter(m: BaseModel) -> tuple[str, str]:
    assert isinstance(m, CounterResponse)
    return m.proposal.retailer_id, m.proposal.vendor_id


def _signed(m: BaseModel) -> tuple[str, str]:
    assert isinstance(m, SignedOrder)
    return m.proposal.vendor_id, m.proposal.retailer_id


def _terms(m: BaseModel) -> tuple[str, str]:
    assert isinstance(m, TermsProposal)
    return m.retailer_id, m.vendor_id


def _vendor_only(m: BaseModel) -> tuple[str, str]:
    """Vendor → any retailer: the body names the vendor only."""
    vendor_id = getattr(m, "vendor_id", None) or getattr(getattr(m, "vendor", None), "id", "")
    return str(vendor_id), ""


def _to_vendor(m: BaseModel) -> tuple[str, str]:
    """Retailer → vendor where the body names the vendor only."""
    return "", str(getattr(m, "vendor_id", ""))


SKILL_POLICY: dict[str, SkillRule] = {
    # retailer → vendor
    "order.propose": SkillRule("retailer", "vendor", OrderProposal, Decision, _order),
    "order.counter_response": SkillRule("retailer", "vendor", CounterResponse, Decision, _counter),
    "terms.propose": SkillRule("retailer", "vendor", TermsProposal, Ack, _terms),
    "vendor.status": SkillRule("retailer", "vendor", VendorStatusUpdate, Ack, _to_vendor),
    "info.request": SkillRule(
        "retailer",
        "vendor",
        InfoRequestMessage,
        Ack,
        lambda m: (m.retailer_id, m.vendor_id) if isinstance(m, InfoRequestMessage) else ("", ""),
    ),
    "listing.status": SkillRule(
        "retailer",
        "vendor",
        ListingStatusUpdate,
        Ack,
        lambda m: (m.retailer_id, m.vendor_id) if isinstance(m, ListingStatusUpdate) else ("", ""),
    ),
    "vendor.invite": SkillRule(
        "retailer",
        "vendor",
        VendorInvite,
        Ack,
        lambda m: (m.retailer_id, m.vendor_id) if isinstance(m, VendorInvite) else ("", ""),
    ),
    # vendor → retailer
    "order.vendor_signed": SkillRule("vendor", "retailer", SignedOrder, Ack, _signed),
    "terms.response": SkillRule(
        "vendor",
        "retailer",
        TermsResponse,
        Ack,
        lambda m: (m.vendor_id, m.retailer_id) if isinstance(m, TermsResponse) else ("", ""),
    ),
    "info.response": SkillRule(
        "vendor",
        "retailer",
        InfoResponseMessage,
        Ack,
        lambda m: (m.vendor_id, m.retailer_id) if isinstance(m, InfoResponseMessage) else ("", ""),
    ),
    "order.vendor_declined": SkillRule(
        "vendor",
        "retailer",
        VendorDeclined,
        Ack,
        lambda m: (m.vendor_id, m.retailer_id) if isinstance(m, VendorDeclined) else ("", ""),
    ),
    "vendor.onboard": SkillRule(
        "vendor", "retailer", VendorApplication, VendorCheckOut, _vendor_only
    ),
    "listing.submit": SkillRule(
        "vendor", "retailer", ListingSubmission, ListingStepOut, _vendor_only
    ),
    "listing.confirm": SkillRule("vendor", "retailer", Listing, Ack, _vendor_only),
}
