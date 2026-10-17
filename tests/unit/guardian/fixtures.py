from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from vl_core.schemas import (
    AutoAccept,
    OrderLine,
    OrderProposal,
    SkuRule,
    StandingMandate,
    TimeWindow,
)

IST = ZoneInfo("Asia/Kolkata")


def maya_mandate(**changes: object) -> StandingMandate:
    m = StandingMandate(
        vendor_id="vendor:maya",
        version=1,
        currency="INR",
        timezone="Asia/Kolkata",
        skus=[
            SkuRule(sku="spinach", unit="bunch", price_floor=Decimal(35), max_qty_per_day=300),
            SkuRule(sku="tomato", unit="kg", price_floor=Decimal(60), max_qty_per_day=100),
        ],
        pickup_windows=[TimeWindow(start=time(6), end=time(10))],
        payment_days_max=7,
        approved_buyers=["retailer:freshcart"],
        auto_accept=AutoAccept(max_value=Decimal(10000)),
        signature="signed",
    )
    return m.model_copy(update=changes)


def proposal(
    qty: int = 350,
    at: str = "05:30",
    price: int = 40,
    days: int = 7,
    buyer: str = "retailer:freshcart",
    sku: str = "spinach",
    unit: str = "bunch",
    currency: str = "INR",
    order_id: str = "ord-hub4",
) -> OrderProposal:
    hh, mm = map(int, at.split(":"))
    return OrderProposal(
        order_id=order_id,
        version=1,
        retailer_id=buyer,
        vendor_id="vendor:maya",
        store_id="store:hub-4",
        lines=(OrderLine(sku=sku, qty=Decimal(qty), unit=unit, price=Decimal(price)),),
        pickup_at=datetime(2026, 10, 15, hh, mm, tzinfo=IST),
        payment_days=days,
        currency=currency,
        terms_version="qc-standard-v1",
    )
