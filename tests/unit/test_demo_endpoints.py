"""Hosted demo (ADR 0014): /dev/* answers only the admin token; the dev outbox stays closed."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from retailer_svc.app import create_app as create_retailer
from trust_gateway.app import create_app as create_gateway
from vl_core.a2a import ADMIN_HEADER
from vl_core.crypto import LocalSigner
from vl_core.devguard import require_demo
from vl_core.settings import Settings

ADMIN = "test-admin"
OPERATOR = "test-operator"
# Explicit flags: Settings() also reads a developer's .env, where VL_DEV_ENDPOINTS is on.
CLOSED = {"vl_dev_endpoints": False, "vl_admin_token": "", "vl_operator_token": ""}
HOSTED = Settings(
    **{**CLOSED, "vl_admin_token": ADMIN, "vl_operator_token": OPERATOR},
    vl_demo_endpoints=True,
)


def test_dev_endpoints_open_without_a_token() -> None:
    require_demo(Settings(vl_dev_endpoints=True), "reset")


@pytest.mark.parametrize(
    ("settings", "admin", "operator"),
    [
        (
            Settings(**{**CLOSED, "vl_admin_token": ADMIN}),
            ADMIN,
            None,
        ),  # demo flag off: closed even to admin
        (HOSTED, None, None),
        (HOSTED, "wrong", None),
        (HOSTED, None, "wrong"),
        (Settings(**CLOSED, vl_demo_endpoints=True), "", None),  # no admin token configured
    ],
)
def test_refused(settings: Settings, admin: str | None, operator: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        require_demo(settings, "reset", admin, operator)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(("admin", "operator"), [(ADMIN, None), (None, OPERATOR)])
def test_allowed(admin: str | None, operator: str | None) -> None:
    require_demo(HOSTED, "tamper", admin, operator)


def test_gateway_reset_and_tamper_over_http() -> None:
    client = TestClient(create_gateway(HOSTED, LocalSigner()))
    assert client.post("/dev/reset").status_code == 404
    assert client.post("/dev/reset", headers={ADMIN_HEADER: "wrong"}).status_code == 404
    assert client.post("/dev/reset", headers={ADMIN_HEADER: ADMIN}).status_code == 200
    # The console's operator may tamper (the audit-chain demo), but not reset.
    assert client.post("/dev/reset", headers={"X-VL-Operator-Token": OPERATOR}).status_code == 404
    assert client.post("/dev/tamper/0").status_code == 404
    # Reset left no audit records, so the token is accepted and record 0 is not found.
    response = client.post("/dev/tamper/0", headers={"X-VL-Operator-Token": OPERATOR})
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


def test_retailer_outbox_stays_closed() -> None:
    client = TestClient(create_retailer(HOSTED, LocalSigner()))
    assert client.get("/dev/outbox", headers={ADMIN_HEADER: ADMIN}).status_code == 404
    assert client.post("/dev/tick").status_code == 404
    assert client.post("/dev/tick", headers={ADMIN_HEADER: ADMIN}).status_code == 200
