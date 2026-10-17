from decimal import Decimal

import pytest

from vl_core.crypto import b64d, b64e, canonical_json, hash_obj
from vl_core.schemas import OrderLine


def test_sorted_keys_no_whitespace_utf8() -> None:
    assert canonical_json({"b": 1, "a": "పాలకూర"}) == '{"a":"పాలకూర","b":1}'.encode()


def test_key_order_does_not_change_hash() -> None:
    assert hash_obj({"x": 1, "y": [1, 2]}) == hash_obj({"y": [1, 2], "x": 1})


def test_nan_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_equal_decimals_hash_equal() -> None:
    a = OrderLine(sku="spinach", qty=Decimal("300"), unit="bunch", price=Decimal("40"))
    b = OrderLine(sku="spinach", qty=Decimal("300.00"), unit="bunch", price=Decimal("40.0"))
    assert hash_obj(a) == hash_obj(b)
    assert canonical_json(a) == b'{"price":"40","qty":"300","sku":"spinach","unit":"bunch"}'


def test_model_and_dict_hash_the_same() -> None:
    line = OrderLine(sku="spinach", qty=Decimal(1), unit="bunch", price=Decimal("1.5"))
    assert hash_obj(line) == hash_obj(line.model_dump(mode="json"))


def test_b64_roundtrip() -> None:
    for data in (b"", b"\x00\xff", b"abc"):
        assert b64d(b64e(data)) == data


def test_plain_datetimes_decimals_and_enums_in_dicts() -> None:
    from datetime import UTC, datetime

    from vl_core.schemas import DecisionStatus

    data = {
        "at": datetime(2026, 10, 15, 0, 30, tzinfo=UTC),
        "qty": Decimal("300.00"),
        "status": DecisionStatus.RED,
    }
    assert canonical_json(data) == b'{"at":"2026-10-15T00:30:00+00:00","qty":"300","status":"red"}'
