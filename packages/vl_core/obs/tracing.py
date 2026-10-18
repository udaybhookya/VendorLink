"""OpenTelemetry tracing. Every span is tagged with the agent and who it acts for, and the W3C
`traceparent` header is propagated on every HTTP call, so one order can be followed across all
agents and services (Cloud Trace in deploy; the dashboard uses the same trace ids locally)."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context, propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vl_core.obs import get_logger, party_var, service_var, trace_id_var
from vl_core.settings import Settings, get_settings

_configured = False
QUIET_PATHS = frozenset({"/healthz", "/health"})
_READS = frozenset({"GET", "HEAD", "OPTIONS"})
# Platform plumbing: registration and telemetry. Logged at DEBUG unless they fail.
PLUMBING = ("/credentials", "/events", "/agents/hosted")


def configure_tracing(settings: Settings | None = None) -> None:
    """Install one tracer provider per process. Exporter by VL_TRACE; ids are always real."""
    global _configured
    if _configured:
        return
    settings = settings or get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    if settings.vl_trace == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif settings.vl_trace == "cloud":
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def _tracer() -> trace.Tracer:
    configure_tracing()
    return trace.get_tracer("vendorlink")


def current_ids() -> tuple[str, str]:
    """(trace_id, span_id) as hex of the current span, or empty strings."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


@contextmanager
def agent_span(name: str, *, agent_id: str, on_behalf_of: str = "", **attrs: Any) -> Iterator[Any]:
    attributes = {"vl.agent_id": agent_id, "vl.on_behalf_of": on_behalf_of}
    attributes.update({f"vl.{k}": v for k, v in attrs.items() if v is not None})
    with _tracer().start_as_current_span(name, attributes=attributes) as span:
        token = trace_id_var.set(current_ids()[0])
        try:
            yield span
        finally:
            trace_id_var.reset(token)


def inject(headers: MutableMapping[str, str]) -> None:
    propagate.inject(headers)


PARTY_PATH = re.compile(r"^/(vendors|retailers)/((?:vendor|retailer):[^/]+)")
A2A_PATH = re.compile(r"^/a2a/((?:vendor|retailer)-agent):([^/]+)/")


def party_from_path(path: str) -> str | None:
    if m := PARTY_PATH.match(path):
        return m.group(2)
    if m := A2A_PATH.match(path):
        return f"{m.group(1).removesuffix('-agent')}:{m.group(2)}"
    return None


class TracingMiddleware:
    """Continue the caller's trace (traceparent), open a server span for each request, and log
    one line per request: changes at INFO, failures at WARNING/ERROR (with the traceback), and
    routine reads (the screens poll) at DEBUG."""

    def __init__(self, app: ASGIApp, service: str) -> None:
        self.app = app
        self.service = service
        self.log = get_logger("http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        carrier = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = context.attach(propagate.extract(carrier))
        method, path = scope.get("method", ""), scope.get("path", "")
        party = party_from_path(path)
        party_token = party_var.set(party)
        service_token = service_var.set(self.service)
        status = 500
        started_response = False
        started = time.perf_counter()

        async def send_status(message: Message) -> None:
            nonlocal status, started_response
            if message["type"] == "http.response.start":
                status, started_response = int(message["status"]), True
            await send(message)

        try:
            with agent_span(
                f"{method} {path}",
                agent_id=self.service,
                on_behalf_of=party or "",
                service=self.service,
            ):
                try:
                    await self.app(scope, receive, send_status)
                except Exception as exc:
                    self.log.exception(
                        f"{method} {path} failed", ms=_ms(started), caller=_caller(carrier)
                    )
                    if started_response:
                        raise
                    # Answer here, inside the CORS layer, so the browser shows the error
                    # instead of a bare "Failed to fetch".
                    await _internal_error(send, exc)
                    return
                if path not in QUIET_PATHS:
                    self._log_request(method, path, status, started, carrier)
        finally:
            service_var.reset(service_token)
            party_var.reset(party_token)
            context.detach(token)

    def _log_request(
        self, method: str, path: str, status: int, started: float, headers: dict[str, str]
    ) -> None:
        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        elif method in _READS or _caller(headers) or path.startswith(PLUMBING):
            level = logging.DEBUG  # reads, and calls between services (logged as events)
        else:
            level = logging.INFO  # a person or script changed something
        self.log.log(
            level,
            f"{method} {path} → {status}",
            ms=_ms(started),
            caller=_caller(headers),
        )


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _caller(headers: dict[str, str]) -> str | None:
    """The calling agent, when the request is signed (service-to-service calls)."""
    return headers.get("x-vl-agent") or None


async def _internal_error(send: Send, exc: Exception) -> None:
    body = json.dumps(
        {"detail": {"error": "internal_error", "detail": type(exc).__name__}}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
