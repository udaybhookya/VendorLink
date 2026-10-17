from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agents.core.guardian.rules import evaluate, local_pickup
from tests.unit.guardian.fixtures import IST, maya_mandate, proposal
from vl_core.schemas import DecisionStatus, ReasonCode

NONE: dict[str, Decimal] = {}


def codes(decision) -> set[ReasonCode]:  # type: ignore[no-untyped-def]
    return {r.code for r in decision.reasons}


def test_hub4_demo_anchor_red_with_counter_300_at_0600() -> None:
    d = evaluate(proposal(350, "05:30"), maya_mandate(), NONE)
    assert d.status == DecisionStatus.RED
    assert codes(d) == {ReasonCode.QTY_OVER_DAILY_MAX, ReasonCode.PICKUP_BEFORE_WINDOW}
    assert d.counter is not None and d.counter.version == 2
    assert d.counter.lines[0].qty == 300
    assert local_pickup(d.counter, maya_mandate()).strftime("%H:%M") == "06:00"
    # The accepted counter needs Maya's OK: within rules but over her auto-accept value.
    again = evaluate(d.counter, maya_mandate(), NONE)
    assert again.status == DecisionStatus.AMBER
    assert codes(again) == {ReasonCode.VALUE_OVER_AUTO_ACCEPT}


def test_green_within_rules_and_under_auto_accept() -> None:
    d = evaluate(proposal(100, "07:00"), maya_mandate(), NONE)
    assert d.status == DecisionStatus.GREEN and d.reasons == [] and d.counter is None


@pytest.mark.parametrize(
    ("kwargs", "code", "status"),
    [
        ({"qty": 301, "at": "07:00"}, ReasonCode.QTY_OVER_DAILY_MAX, DecisionStatus.RED),
        ({"qty": 100, "at": "05:59"}, ReasonCode.PICKUP_BEFORE_WINDOW, DecisionStatus.RED),
        ({"qty": 100, "at": "10:01"}, ReasonCode.PICKUP_AFTER_WINDOW, DecisionStatus.RED),
        (
            {"qty": 100, "at": "07:00", "price": 34},
            ReasonCode.PRICE_BELOW_FLOOR,
            DecisionStatus.RED,
        ),
        ({"qty": 100, "at": "07:00", "days": 8}, ReasonCode.PAYMENT_TOO_LATE, DecisionStatus.RED),
        ({"qty": 280, "at": "07:00"}, ReasonCode.VALUE_OVER_AUTO_ACCEPT, DecisionStatus.AMBER),
        (
            {"buyer": "retailer:other", "at": "07:00", "qty": 10},
            ReasonCode.BUYER_NOT_APPROVED,
            DecisionStatus.DECLINE,
        ),
        (
            {"sku": "mint", "at": "07:00", "qty": 10},
            ReasonCode.SKU_NOT_IN_MANDATE,
            DecisionStatus.DECLINE,
        ),
        (
            {"unit": "kg", "at": "07:00", "qty": 10},
            ReasonCode.SKU_NOT_IN_MANDATE,
            DecisionStatus.DECLINE,
        ),
        (
            {"currency": "IDR", "at": "07:00", "qty": 10},
            ReasonCode.CURRENCY_MISMATCH,
            DecisionStatus.DECLINE,
        ),
    ],
)
def test_each_reason_code(
    kwargs: dict[str, object], code: ReasonCode, status: DecisionStatus
) -> None:
    d = evaluate(proposal(**kwargs), maya_mandate(), NONE)  # type: ignore[arg-type]
    assert d.status == status
    assert code in codes(d)


def test_counter_fixes_price_payment_and_after_window_moves_to_next_day() -> None:
    d = evaluate(proposal(100, "11:00", price=30, days=30), maya_mandate(), NONE)
    assert d.status == DecisionStatus.RED and d.counter is not None
    assert d.counter.lines[0].price == 35 and d.counter.payment_days == 7
    local = local_pickup(d.counter, maya_mandate())
    assert (local.day, local.strftime("%H:%M")) == (16, "06:00")


def test_committed_quantity_reduces_allowance() -> None:
    d = evaluate(proposal(150, "07:00"), maya_mandate(), {"spinach": Decimal(200)})
    assert d.status == DecisionStatus.RED and d.counter is not None
    assert d.counter.lines[0].qty == 100
    full = evaluate(proposal(1, "07:00"), maya_mandate(), {"spinach": Decimal(300)})
    assert full.status == DecisionStatus.DECLINE
    assert codes(full) == {ReasonCode.DAILY_MAX_REACHED}


def test_unsigned_or_missing_mandate_needs_vendor() -> None:
    for mandate in (None, maya_mandate(signature=None)):
        d = evaluate(proposal(10, "07:00"), mandate, NONE)
        assert d.status == DecisionStatus.AMBER
        assert codes(d) == {ReasonCode.NO_STANDING_MANDATE}


def test_pickup_compared_in_mandate_timezone() -> None:
    # 01:00 UTC is 06:30 in India: inside the window.
    p = proposal(10, "07:00").model_copy(
        update={"pickup_at": datetime(2026, 10, 15, 1, 0, tzinfo=UTC)}
    )
    assert evaluate(p, maya_mandate(), NONE).status == DecisionStatus.GREEN
    assert local_pickup(p, maya_mandate()).tzinfo is not None
    assert IST is not None
