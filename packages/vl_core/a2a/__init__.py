"""A2A envelopes, request signing, the gateway client and the receiving router.

A caller signs `canonical_json({method, path, body_hash, jti})` with its agent key; the gateway
verifies against the public key registered for that agent, and signs again when it forwards.
"""

from vl_core.a2a.client import ADMIN_HEADER, GatewayClient, GatewayError, register_credential
from vl_core.a2a.http import make_client, set_transport
from vl_core.a2a.identity import agent_id_for, party_id_for
from vl_core.a2a.server import (
    GATEWAY_AGENT_ID,
    GATEWAY_KEY_PATH,
    GatewayVerifier,
    SkillHandler,
    a2a_router,
)
from vl_core.a2a.signing import (
    HEADER_AGENT,
    HEADER_JTI,
    HEADER_SIGNATURE,
    make_envelope,
    request_signing_input,
    sign_request,
    verify_request,
)

__all__ = [
    "ADMIN_HEADER",
    "GATEWAY_AGENT_ID",
    "GATEWAY_KEY_PATH",
    "HEADER_AGENT",
    "HEADER_JTI",
    "HEADER_SIGNATURE",
    "GatewayClient",
    "GatewayError",
    "GatewayVerifier",
    "SkillHandler",
    "a2a_router",
    "agent_id_for",
    "make_client",
    "make_envelope",
    "party_id_for",
    "register_credential",
    "request_signing_input",
    "set_transport",
    "sign_request",
    "verify_request",
]
