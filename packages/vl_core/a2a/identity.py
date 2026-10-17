"""Naming convention: party `vendor:maya` is represented by agent `vendor-agent:maya`."""

from __future__ import annotations


def agent_id_for(party_id: str) -> str:
    kind, _, name = party_id.partition(":")
    if not name:
        raise ValueError(f"party id must look like kind:name, got {party_id!r}")
    return f"{kind}-agent:{name}"


def party_id_for(agent_id: str) -> str:
    kind, _, name = agent_id.partition(":")
    if not kind.endswith("-agent") or not name:
        raise ValueError(f"not a party agent id: {agent_id!r}")
    return f"{kind.removesuffix('-agent')}:{name}"
