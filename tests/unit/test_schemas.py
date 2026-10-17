from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from vl_core.crypto import hash_obj
from vl_core.schemas import (
    AgentCard,
    AutoAccept,
    OrderLine,
    OrderProposal,
    SkuRule,
    StandingMandate,
    TimeWindow,
)


def hub4_order() -> OrderProposal:
    return OrderProposal(
        order_id="ord-1",
        version=1,
        retailer_id="retailer:quickbasket",
        vendor_id="vendor:maya",
        store_id="hub-4",
        lines=(OrderLine(sku="spinach", qty=Decimal(350), unit="bunch", price=Decimal(40)),),
        pickup_at=datetime(2026, 10, 15, 0, 0, tzinfo=UTC),
        payment_days=7,
        currency="INR",
        terms_version="qc-standard-v1",
    )


def test_order_versions_are_immutable() -> None:
    v1 = hub4_order()
    with pytest.raises(ValidationError):
        v1.payment_days = 3  # type: ignore[misc]


def test_counter_is_next_version_and_leaves_original() -> None:
    v1 = hub4_order()
    line = v1.lines[0].model_copy(update={"qty": Decimal(300)})
    v2 = v1.next_version(lines=(line,))
    assert (v1.version, v2.version) == (1, 2)
    assert v1.lines[0].qty == 350 and v2.lines[0].qty == 300
    assert v1.total_value() == 14000 and v2.total_value() == 12000
    assert hash_obj(v1) != hash_obj(v2)


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        OrderLine.model_validate({"sku": "x", "qty": 1, "unit": "kg", "price": 1, "extra": 1})


def test_naive_pickup_time_rejected() -> None:
    data = hub4_order().model_dump()
    data["pickup_at"] = datetime(2026, 10, 15, 5, 30)
    with pytest.raises(ValidationError):
        OrderProposal.model_validate(data)


def test_mandate_unsigned_drops_signature_and_consent_ref() -> None:
    mandate = StandingMandate(
        vendor_id="vendor:maya",
        version=1,
        currency="INR",
        timezone="Asia/Kolkata",
        skus=[SkuRule(sku="spinach", unit="bunch", price_floor=Decimal(35), max_qty_per_day=300)],
        pickup_windows=[TimeWindow(start=time(6), end=time(10))],
        payment_days_max=7,
        approved_buyers=["retailer:quickbasket"],
        auto_accept=AutoAccept(max_value=Decimal(10000)),
        signature="sig",
        signed_at=datetime.now(UTC),
        consent_ref="consent-1",
    )
    unsigned = mandate.unsigned()
    assert unsigned.signature is None and unsigned.signed_at is None
    assert unsigned.consent_ref is None
    assert unsigned.skus == mandate.skus
    assert mandate.rule_for("spinach") is not None and mandate.rule_for("mint") is None


def test_time_window_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        TimeWindow(start=time(10), end=time(6))


def test_agent_card_transport_needs_target() -> None:
    base = {
        "agent_id": "a",
        "name": "A",
        "kind": "specialist",
        "tenant": "retailer",
        "skills": [{"capability": "x.y", "input_schema": "I", "output_schema": "O"}],
    }
    with pytest.raises(ValidationError):
        AgentCard.model_validate({**base, "transport": "local"})
    with pytest.raises(ValidationError):
        AgentCard.model_validate({**base, "transport": "a2a"})
    card = AgentCard.model_validate({**base, "transport": "a2a", "endpoint": "http://x"})
    assert card.capabilities == ["x.y"]


def test_agent_card_rules() -> None:
    skill = {"capability": "x.y", "input_schema": "I", "output_schema": "O"}
    base = {
        "agent_id": "a",
        "name": "A",
        "kind": "core",
        "tenant": "vendor",
        "transport": "a2a",
        "endpoint": "http://x",
    }
    with pytest.raises(ValidationError):
        AgentCard.model_validate({**base, "skills": [skill, skill]})  # duplicate capability
    with pytest.raises(ValidationError):
        AgentCard.model_validate({**base, "agent_id": "Bad_Id", "skills": [skill]})
    card = AgentCard.model_validate(
        {**base, "skills": [skill, {**skill, "capability": "x.secret", "internal": True}]}
    )
    assert card.public().capabilities == ["x.y"]


def test_endpoints_expand_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from vl_core.schemas import expand_env

    monkeypatch.setenv("GRADER_URL", "http://grader:8101")
    assert expand_env("${GRADER_URL}") == "http://grader:8101"
    assert expand_env("${MISSING_URL:-http://localhost:9}") == "http://localhost:9"
