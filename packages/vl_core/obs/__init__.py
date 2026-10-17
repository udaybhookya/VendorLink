"""Structured logging with trace context.

    log = get_logger("vendor")
    log.info("deal decided", order_id=oid, status="red", reasons="QTY_OVER_DAILY_MAX")

Keyword arguments become fields. Every line also carries the service, the party the request
acts for, and the trace id, so one order can be followed across all four services.

Output (`VL_LOG_FORMAT`): `pretty` is one readable line per event for a terminal, `json` is one
JSON object per line for Cloud Logging, and `auto` (the default) picks pretty on a terminal and
JSON otherwise. Level: `LOG_LEVEL` (DEBUG adds routine reads such as the screens' polling).

Never log raw documents, audio, chat text or ID numbers; log ids, codes, counts and hashes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
agent_id_var: ContextVar[str | None] = ContextVar("agent_id", default=None)
vendor_id_var: ContextVar[str | None] = ContextVar("vendor_id", default=None)
party_var: ContextVar[str | None] = ContextVar("party", default=None)
"""The vendor or retailer the current request acts for (set from the request path)."""
service_var: ContextVar[str | None] = ContextVar("service", default=None)
"""The service handling the current request (set by the request middleware)."""

_CONTEXT_VARS = {
    "trace_id": trace_id_var,
    "agent_id": agent_id_var,
    "vendor_id": vendor_id_var,
    "party": party_var,
}
_default_service = ""


def new_trace_id() -> str:
    return uuid.uuid4().hex


def bind(
    *, trace_id: str | None = None, agent_id: str | None = None, vendor_id: str | None = None
) -> None:
    """Set trace context for the current task; unset arguments are left as they are."""
    if trace_id is not None:
        trace_id_var.set(trace_id)
    if agent_id is not None:
        agent_id_var.set(agent_id)
    if vendor_id is not None:
        vendor_id_var.set(vendor_id)


def set_service(name: str) -> None:
    """Name the service for log lines outside a request (startup, background loops)."""
    global _default_service
    _default_service = name
    service_var.set(name)


def _service() -> str:
    return service_var.get() or _default_service


def _context(record: logging.LogRecord) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    for key, var in _CONTEXT_VARS.items():
        value = var.get()
        if value is not None:
            entry[key] = value
    extra = getattr(record, "fields", None)
    if isinstance(extra, dict):
        entry.update(extra)
    return entry


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            # Cloud Logging reads `severity`; keep `level` for local tools and tests.
            "severity": record.levelname,
            "service": _service(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(_context(record))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


_COLOURS = {"DEBUG": "2", "INFO": "36", "WARNING": "33", "ERROR": "31", "CRITICAL": "41"}


class PrettyFormatter(logging.Formatter):
    """One readable line per event, for a terminal:
    `06:21:25.323 INFO    vendor    agent.concierge  chat turn  mode=llm ms=840  vendor:maya · 24a2`
    """

    def __init__(self, colour: bool) -> None:
        super().__init__()
        self.colour = colour

    def _paint(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.colour else text

    def format(self, record: logging.LogRecord) -> str:
        context = _context(record)
        trace = str(context.pop("trace_id", ""))[:8]
        party = str(context.pop("party", "") or "")
        if context.get("vendor_id") == party:
            context.pop("vendor_id")
        clock = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = self._paint(_COLOURS.get(record.levelname, "0"), f"{record.levelname:<7}")
        name = record.name.removeprefix("vl.")
        fields = " ".join(f"{k}={_value(v)}" for k, v in context.items() if v not in (None, ""))
        tail = " · ".join(t for t in (party, trace) if t)
        line = (
            f"{self._paint('2', clock)} {level} {_service():<13} {self._paint('35', f'{name:<16}')}"
            f" {record.getMessage()}"
        )
        if fields:
            line += f"  {fields}"
        if tail:
            line += f"  {self._paint('2', tail)}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _value(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = text if len(text) <= 200 else text[:199] + "…"
    return json.dumps(text, ensure_ascii=False) if (" " in text or not text) else text


class VlLogger(logging.LoggerAdapter[logging.Logger]):
    """A logger whose keyword arguments become structured fields. A ready-made dict of fields
    goes in `fields=`: `log.info("agent call", ms=12, fields=attrs)`."""

    _RESERVED = frozenset({"exc_info", "stack_info", "stacklevel", "extra"})

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, Any]:
        given = kwargs.pop("fields", None)
        fields = {k: kwargs.pop(k) for k in list(kwargs) if k not in self._RESERVED}
        if isinstance(given, dict):
            fields = {**given, **fields}
        if fields:
            extra = dict(kwargs.get("extra") or {})
            extra["fields"] = {**extra.get("fields", {}), **fields}
            kwargs["extra"] = extra
        return msg, kwargs


_configured = False


def _formatter(fmt: str) -> logging.Formatter:
    tty = sys.stdout.isatty()
    if fmt == "json" or (fmt == "auto" and not tty):
        return JsonFormatter()
    return PrettyFormatter(colour=tty and "NO_COLOR" not in os.environ)


def _configure(level: str, fmt: str) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_formatter(fmt))
    root = logging.getLogger("vl")
    root.handlers = [handler]
    root.setLevel(level.upper())
    root.propagate = False
    _configured = True


def get_logger(name: str) -> VlLogger:
    """Logger under the `vl.` namespace: `log.info("what happened", key=value, ...)`."""
    from vl_core.settings import get_settings

    settings = get_settings()
    _configure(settings.log_level, settings.vl_log_format)
    return VlLogger(logging.getLogger(f"vl.{name}"), {})


def log_startup(service: str, settings: Any) -> None:
    """One line per service at start: which backends, models and channels it runs with."""
    set_service(service)
    get_logger("startup").info(
        f"{service} starting",
        store=settings.vl_store,
        blob=settings.vl_blob,
        signer=settings.vl_signer,
        screen=settings.vl_screen,
        channels=settings.vl_channels,
        checkpoints=settings.vl_checkpoints,
        flash_model=settings.flash_model or "off",
        pro_model=settings.pro_model or "off",
        live_model=settings.live_model or "off",
        dev_endpoints=settings.vl_dev_endpoints,
        demo_endpoints=settings.vl_demo_endpoints,
        log_level=settings.log_level,
    )
