"""Capability matching shared by the agent registry and anything that reads agent cards."""

from __future__ import annotations


class RegistryError(Exception):
    pass


def capability_matches(pattern: str, capability: str) -> bool:
    """`grade.*` matches `grade.produce`; anything else must match exactly."""
    if pattern.endswith(".*"):
        return capability.startswith(pattern[:-1])
    return pattern == capability


__all__ = ["RegistryError", "capability_matches"]
