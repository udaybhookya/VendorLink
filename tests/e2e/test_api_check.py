"""Every route of every service is exercised by the v1 journey, with no server errors."""

from scripts import api_check


def test_every_route_is_exercised(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("sys.argv", ["api_check"])
    assert api_check.main() == 0
