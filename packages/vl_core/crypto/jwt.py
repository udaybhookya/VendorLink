"""Minimal EdDSA JWTs signed through the Signer interface (so KMS keys work unchanged)."""

from __future__ import annotations

import json
from typing import Any

from vl_core.crypto.canonical import b64d, b64e, canonical_json
from vl_core.crypto.signer import Signer, verify_signature


class JwtError(Exception):
    pass


async def jwt_encode(signer: Signer, key_id: str, claims: dict[str, Any]) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": key_id}
    signing_input = f"{b64e(canonical_json(header))}.{b64e(canonical_json(claims))}"
    signature = await signer.sign(key_id, signing_input.encode("ascii"))
    return f"{signing_input}.{b64e(signature)}"


def jwt_decode(token: str, public_key: bytes, *, audience: str, now: float) -> dict[str, Any]:
    """Verify signature, `aud` and `exp`; return the claims. Raises JwtError."""
    try:
        header_b64, claims_b64, sig_b64 = token.split(".")
        header = json.loads(b64d(header_b64))
        claims = json.loads(b64d(claims_b64))
        signature = b64d(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise JwtError("malformed token") from exc
    if not isinstance(header, dict) or header.get("alg") != "EdDSA":
        raise JwtError("unsupported alg")
    if not verify_signature(public_key, f"{header_b64}.{claims_b64}".encode("ascii"), signature):
        raise JwtError("bad token signature")
    if not isinstance(claims, dict):
        raise JwtError("malformed claims")
    if claims.get("aud") != audience:
        raise JwtError("wrong audience")
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or exp < now:
        raise JwtError("token expired")
    return claims
