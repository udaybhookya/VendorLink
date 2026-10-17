"""One place that builds HTTP clients, so tests can route all services in-process and every
call carries the W3C trace context."""

from __future__ import annotations

import httpx

from vl_core.obs.tracing import inject

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Route every client made by `make_client` through `transport` (None restores real HTTP)."""
    global _transport
    _transport = transport


async def _inject_trace(request: httpx.Request) -> None:
    carrier: dict[str, str] = {}
    inject(carrier)
    request.headers.update(carrier)


def make_client(base_url: str, timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        transport=_transport,
        timeout=timeout,
        event_hooks={"request": [_inject_trace]},
    )
