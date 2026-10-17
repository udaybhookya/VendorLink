"""Errors the gateway raises. Kept apart so every gateway module can import them."""

from __future__ import annotations

from typing import Literal

EventType = Literal["bad_token", "bad_signature", "out_of_scope", "injection", "rate"]


class Denied(Exception):
    """A refused request. Optionally raises a SecurityEvent and revokes the caller."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str = "",
        *,
        event: EventType | None = None,
        revoke: bool = False,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.event = event
        self.revoke = revoke
