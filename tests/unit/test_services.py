import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mocks.app import app as mocks_app
from retailer_svc.app import app as retailer_app
from trust_gateway.app import app as gateway_app
from vendor_svc.app import app as vendor_app


@pytest.mark.parametrize(
    ("app", "name"),
    [
        (vendor_app, "vendor-svc"),
        (retailer_app, "retailer-svc"),
        (gateway_app, "trust-gateway"),
        (mocks_app, "mocks"),
    ],
)
def test_healthz(app: FastAPI, name: str) -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == name
