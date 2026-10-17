from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from vl_core.schemas.common import VLModel


class A2AEnvelope(VLModel):
    """Wrapper for every cross-tenant message relayed by the trust gateway.

    `body` holds a dumped vl_core schema model; the receiver validates it per `skill`.
    """

    trace_id: str
    jti: str = Field(description="Unique id; also bound into the request signature")
    skill: str
    from_agent: str
    to_agent: str
    on_behalf_of: str
    sent_at: datetime
    body: dict[str, Any]
