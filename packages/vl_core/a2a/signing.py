from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from vl_core.crypto import Signer, b64d, b64e, canonical_json, sha256_hex, verify_signature
from vl_core.obs import new_trace_id, trace_id_var
from vl_core.schemas import A2AEnvelope

HEADER_AGENT = "X-VL-Agent"
HEADER_JTI = "X-VL-JTI"
HEADER_SIGNATURE = "X-VL-Signature"


def request_signing_input(method: str, path: str, body: bytes, jti: str) -> bytes:
    return canonical_json(
        {"method": method.upper(), "path": path, "body_hash": sha256_hex(body), "jti": jti}
    )


async def sign_request(
    signer: Signer, key_id: str, method: str, path: str, body: bytes, jti: str
) -> str:
    return b64e(await signer.sign(key_id, request_signing_input(method, path, body, jti)))


def verify_request(
    public_key_b64: str, method: str, path: str, body: bytes, jti: str, signature_b64: str
) -> bool:
    try:
        public_key, signature = b64d(public_key_b64), b64d(signature_b64)
    except ValueError:
        return False
    return verify_signature(public_key, request_signing_input(method, path, body, jti), signature)


def make_envelope(
    *,
    skill: str,
    from_agent: str,
    to_agent: str,
    on_behalf_of: str,
    body: BaseModel,
    jti: str | None = None,
) -> A2AEnvelope:
    return A2AEnvelope(
        trace_id=trace_id_var.get() or new_trace_id(),
        jti=jti or uuid.uuid4().hex,
        skill=skill,
        from_agent=from_agent,
        to_agent=to_agent,
        on_behalf_of=on_behalf_of,
        sent_at=datetime.now(UTC),
        body=body.model_dump(mode="json"),
    )
