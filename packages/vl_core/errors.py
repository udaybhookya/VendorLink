from __future__ import annotations

import logging

from fastapi.responses import JSONResponse


class Problem(Exception):
    """A refused action with a machine code (rendered to the vendor elsewhere)."""

    def __init__(self, status: int, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


def error_response(status: int, code: str, detail: str = "") -> JSONResponse:
    """The API's error body, `{"detail": {"error": code, "detail": ...}}`; logs why."""
    from vl_core.obs import get_logger

    level = logging.ERROR if status >= 500 else logging.WARNING
    get_logger("http").log(level, f"refused: {code}", status=status, detail=detail[:200] or None)
    return JSONResponse({"detail": {"error": code, "detail": detail}}, status)
