"""Who may call the demo routes under /dev/ (reset, tick, tamper).

Locally `VL_DEV_ENDPOINTS=true` opens them. A hosted demo sets `VL_DEMO_ENDPOINTS=true` instead:
then a caller must send the admin token (scripts) or, where a route allows it, the operator token
(a person at the console). Anyone else gets 404, as if the route did not exist (ADR 0014).
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException

from vl_core.obs import get_logger
from vl_core.settings import Settings

log = get_logger("dev")


def _matches(token: str | None, expected: str) -> bool:
    return bool(expected) and bool(token) and hmac.compare_digest(token or "", expected)


def require_demo(
    settings: Settings,
    action: str,
    admin_token: str | None = None,
    operator_token: str | None = None,
) -> None:
    """Allow a /dev/ route, or raise 404. Pass `operator_token` only where a person may call it."""
    if settings.vl_dev_endpoints:
        return
    if settings.vl_demo_endpoints:
        if _matches(admin_token, settings.vl_admin_token):
            log.info("demo endpoint called", action=action, actor="admin")
            return
        if _matches(operator_token, settings.vl_operator_token):
            log.info("demo endpoint called", action=action, actor="operator")
            return
        log.warning("demo endpoint refused", action=action)
    raise HTTPException(404)
