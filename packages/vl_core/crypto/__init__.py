"""Canonical JSON, hashing and signing.

Anything that is hashed or signed goes through `canonical_json` first; never `json.dumps`.
"""

from vl_core.crypto.canonical import b64d, b64e, canonical_json, hash_obj, sha256_hex
from vl_core.crypto.jwt import JwtError, jwt_decode, jwt_encode
from vl_core.crypto.signer import KmsSigner, LocalSigner, Signer, get_signer, verify_signature

__all__ = [
    "JwtError",
    "KmsSigner",
    "LocalSigner",
    "Signer",
    "b64d",
    "b64e",
    "canonical_json",
    "get_signer",
    "hash_obj",
    "jwt_decode",
    "jwt_encode",
    "sha256_hex",
    "verify_signature",
]
