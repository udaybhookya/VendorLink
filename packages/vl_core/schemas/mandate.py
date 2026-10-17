from __future__ import annotations

from datetime import datetime, time
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from vl_core.schemas.common import Money, Qty, VLModel


class TimeWindow(VLModel):
    """Local wall-clock window in the mandate's timezone."""

    start: time
    end: time

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("window end must be after start")
        return self


class SkuRule(VLModel):
    sku: str
    unit: str
    price_floor: Money = Field(description="Minimum price per unit")
    max_qty_per_day: Qty


class AutoAccept(VLModel):
    max_value: Money


class StandingMandate(VLModel):
    """The vendor's rules (AP2 'intent' analogue). Signed by the Guardian with consent."""

    vendor_id: str
    version: int = Field(ge=1)
    currency: str
    timezone: str
    skus: list[SkuRule] = Field(min_length=1)
    pickup_windows: list[TimeWindow] = Field(min_length=1)
    payment_days_max: int = Field(ge=0)
    approved_buyers: list[str] = Field(default_factory=list)
    auto_accept: AutoAccept
    signature: str | None = None
    consent_ref: str | None = None
    signed_at: AwareDatetime | None = None

    def unsigned(self) -> StandingMandate:
        """The exact rules the vendor consents to and the Guardian signs.

        Excludes the signature and the consent reference: consent is captured over these rules
        before its id exists.
        """
        return self.model_copy(update={"signature": None, "signed_at": None, "consent_ref": None})

    def rule_for(self, sku: str) -> SkuRule | None:
        return next((rule for rule in self.skus if rule.sku == sku), None)


class ConsentRecord(VLModel):
    id: str
    vendor_id: str
    subject_hash: str = Field(description="sha256(canonical_json(subject)) of what was agreed")
    audio_uri: str | None = None
    transcript: str
    language: str
    captured_at: datetime


class OrderMandate(VLModel):
    """Both parties' signatures over the final order version (AP2 'cart' analogue)."""

    order_id: str
    final_version: int = Field(ge=1)
    terms_hash: str
    vendor_sig: str
    retailer_sig: str
    consent_ref: str | None = None
    created_at: datetime
