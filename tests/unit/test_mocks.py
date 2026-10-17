from datetime import date

import pytest
from fastapi.testclient import TestClient

from mocks.app import create_app
from mocks.registries import names_match


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize(
    ("path", "body", "code"),
    [
        (
            "/id/verify",
            {"doc_type": "aadhaar", "number": "2345 6789 0123", "name": "Maya Thomas"},
            "OK",
        ),
        (
            "/id/verify",
            {"doc_type": "aadhaar", "number": "234567890123", "name": "Maya Rao"},
            "NAME_MISMATCH",
        ),
        ("/id/verify", {"doc_type": "aadhaar", "number": "999999999999", "name": "X"}, "NOT_FOUND"),
        (
            "/bank/verify",
            {
                "doc_type": "ifsc_account",
                "number": "SBIN0001234:123456789012",
                "name": "maya thomas",
            },
            "OK",
        ),
        (
            "/id/verify",
            {"doc_type": "nik", "number": "3174012345670001", "name": "Sara Rahman"},
            "OK",
        ),
    ],
)
def test_registry_post(client: TestClient, path: str, body: dict[str, str], code: str) -> None:
    result = client.post(path, json=body).json()
    assert result["code"] == code and result["simulated"] is True


def test_licences(client: TestClient) -> None:
    assert (
        client.get("/fssai/10019022000123", params={"name": "Maya Thomas"}).json()["code"] == "OK"
    )
    assert (
        client.get("/fssai/10019022000999", params={"name": "Sam Daniel"}).json()["code"]
        == "EXPIRED"
    )
    assert (
        client.get("/pirt/999999999999999", params={"name": "Sara"}).json()["code"] == "NOT_FOUND"
    )


def test_demand(client: TestClient) -> None:
    r = client.get("/demand", params={"store": "store:hub-4", "for_date": date(2026, 10, 15)})
    lines = r.json()["lines"]
    assert lines == [{"sku": "spinach", "qty": 350, "unit": "bunch", "pickup_local": "05:30"}]
    assert (
        client.get("/demand", params={"store": "nope", "for_date": "2026-10-15"}).status_code == 404
    )


def test_name_match() -> None:
    assert names_match("MAYA  thomas", "Maya Thomas")
    assert names_match("Sara", "Sara Rahman")
    assert not names_match("Sam", "Maya Thomas")
    assert not names_match("", "Maya Thomas")
