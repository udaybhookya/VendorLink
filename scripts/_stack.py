"""Run all four services in one process (no ports) for CI, tests and offline demos."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from starlette.types import ASGIApp

from vl_core.a2a import set_transport
from vl_core.settings import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]


class AppRouter(httpx.AsyncBaseTransport):
    """Route requests to in-process ASGI apps by base URL."""

    def __init__(self, apps: dict[str, ASGIApp]) -> None:
        self._transports = {
            httpx.URL(url).netloc: httpx.ASGITransport(app=app) for url, app in apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.netloc)
        if transport is None:
            raise httpx.ConnectError(f"no in-process app for {request.url.netloc!r}")
        return await transport.handle_async_request(request)


APPS: dict[str, Any] = {}
"""The in-process apps of the last stack built (tests reach app.state through this)."""


def build_stack(data_dir: Path | None = None, *, models: bool = False) -> Settings:
    """Create all four apps with in-memory stores and local signers; route HTTP between them.
    `models=True` keeps the models named in .env (otherwise the agents run without a model)."""
    # Self-contained: the developer's .env must not change what the in-process stack uses.
    if not models:
        for var in ("FLASH_MODEL", "PRO_MODEL", "LIVE_MODEL"):
            os.environ[var] = ""
    os.environ["VL_ADMIN_TOKEN"] = "inprocess-admin"
    os.environ["VL_OPERATOR_TOKEN"] = "inprocess-operator"
    os.environ["VL_DEV_ENDPOINTS"] = "true"
    os.environ["VL_CHECKPOINTS"] = "memory"
    os.environ["VL_STORE"] = "memory"
    os.environ["VL_CHANNELS"] = "local"
    os.environ["VL_CONFIG_DIR"] = str(ROOT / "config")
    os.environ["VL_DATA_DIR"] = str(data_dir or ROOT / ".vl_data" / "inprocess")
    get_settings.cache_clear()
    settings = get_settings()

    from mocks.app import create_app as mocks_app
    from retailer_svc.app import create_app as retailer_app
    from trust_gateway.app import create_app as gateway_app
    from vendor_svc.app import create_app as vendor_app
    from vl_core.crypto import LocalSigner

    APPS.clear()
    APPS["gateway"] = gateway_app(settings, LocalSigner())
    APPS["vendor"] = vendor_app(settings, LocalSigner())
    APPS["retailer"] = retailer_app(settings, LocalSigner())
    APPS["mocks"] = mocks_app(settings)
    for host in (APPS["vendor"].state.operations, APPS["retailer"].state.operations):
        host.control.ttl = 0  # in-process: always read fresh status
    set_transport(
        AppRouter(
            {
                settings.trust_gateway_url: APPS["gateway"],
                settings.vendor_svc_url: APPS["vendor"],
                settings.retailer_svc_url: APPS["retailer"],
                settings.mocks_url: APPS["mocks"],
            }
        )
    )
    return settings


def teardown_stack() -> None:
    set_transport(None)
