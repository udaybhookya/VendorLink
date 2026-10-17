from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from vl_core.llm import ChatTurn


@dataclass
class Pending:
    """A subject the vendor was read back and may now say yes to."""

    kind: Literal["deal", "rules", "question"]
    subject_id: str
    subject_hash: str
    readback: str


@dataclass
class ConciergeSession:
    vendor_id: str
    language: str
    history: list[ChatTurn] = field(default_factory=list)
    pending: Pending | None = None
    last_user_text: str = ""
    last_photo_b64: str | None = None
    last_photo_mime: str = "image/jpeg"
