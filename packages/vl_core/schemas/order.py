from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, ConfigDict, Field

from vl_core.schemas.common import Dec, Money, Qty, VLModel


class OrderLine(VLModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sku: str
    qty: Qty
    unit: str
    price: Money = Field(description="Per unit")


class OrderProposal(VLModel):
    """One immutable version of an order. A counter is a new version (see `next_version`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    version: int = Field(ge=1)
    retailer_id: str
    vendor_id: str
    store_id: str
    lines: tuple[OrderLine, ...] = Field(min_length=1)
    pickup_at: AwareDatetime
    payment_days: int = Field(ge=0)
    currency: str
    terms_version: str

    def total_value(self) -> Decimal:
        return sum((line.qty * line.price for line in self.lines), Decimal(0))

    def next_version(self, **changes: Any) -> OrderProposal:
        """Return version + 1 with `changes` applied; this version is left untouched."""
        data = self.model_dump()
        data.update(changes)
        data["version"] = self.version + 1
        return OrderProposal.model_validate(data)


class DecisionStatus(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    DECLINE = "decline"


class ReasonCode(StrEnum):
    QTY_OVER_DAILY_MAX = "QTY_OVER_DAILY_MAX"
    PICKUP_BEFORE_WINDOW = "PICKUP_BEFORE_WINDOW"
    PRICE_BELOW_FLOOR = "PRICE_BELOW_FLOOR"
    BUYER_NOT_APPROVED = "BUYER_NOT_APPROVED"
    PAYMENT_TOO_LATE = "PAYMENT_TOO_LATE"
    VALUE_OVER_AUTO_ACCEPT = "VALUE_OVER_AUTO_ACCEPT"
    PICKUP_AFTER_WINDOW = "PICKUP_AFTER_WINDOW"
    SKU_NOT_IN_MANDATE = "SKU_NOT_IN_MANDATE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    NO_STANDING_MANDATE = "NO_STANDING_MANDATE"
    DAILY_MAX_REACHED = "DAILY_MAX_REACHED"


class Reason(VLModel):
    code: ReasonCode
    params: dict[str, str | int | Dec] = Field(default_factory=dict)


class Decision(VLModel):
    order_id: str
    version: int = Field(ge=1)
    status: DecisionStatus
    counter: OrderProposal | None = None
    reasons: list[Reason] = Field(default_factory=list)
    vendor_sig: str | None = Field(default=None, description="Present when the Guardian signed")
    consent_ref: str | None = None


class SignedOrder(VLModel):
    """The vendor's signature over one exact order version."""

    proposal: OrderProposal
    vendor_sig: str
    consent_ref: str | None = None


class CounterResponse(VLModel):
    """The retailer's answer to a counter: accept or reject that exact version."""

    order_id: str
    accept: bool
    proposal: OrderProposal
    note: str = ""


DealStatus = Literal["open", "countered", "awaiting_consent", "signed", "declined", "rejected"]


class Deal(VLModel):
    """Vendor-side view of one order thread."""

    order_id: str
    retailer_id: str
    vendor_id: str
    store_id: str
    versions: list[OrderProposal]
    decisions: list[Decision] = Field(default_factory=list)
    status: DealStatus
    vendor_sig: str | None = None
    consent_ref: str | None = None
    updated_at: datetime

    @property
    def latest(self) -> OrderProposal:
        return self.versions[-1]


RetailerOrderStatus = Literal[
    "queued", "proposed", "countered", "awaiting_vendor", "confirmed", "declined", "rejected"
]


class RetailerOrder(VLModel):
    """Retailer-side view of one order thread."""

    order_id: str
    retailer_id: str
    vendor_id: str
    store_id: str
    versions: list[OrderProposal]
    decisions: list[Decision] = Field(default_factory=list)
    status: RetailerOrderStatus
    queue_reason: str | None = None
    mandate_terms_hash: str | None = None
    updated_at: datetime

    @property
    def latest(self) -> OrderProposal:
        return self.versions[-1]
