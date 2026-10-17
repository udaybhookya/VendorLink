"""Keys of `AgentContext.resources`: what a host hands its in-process agents.

A host (vendor-svc, retailer-svc) sets these; agents read them. Keeping the keys here, not inside
one agent, means services never import a particular agent to wire it up (CLAUDE.md rule 5).
"""

from __future__ import annotations

VENDOR_PORT = "vendor_port"
"""Concierge → vendor state (`agents.core.concierge.ports.VendorPort`). Vendor host only."""

CONTACTS = "contacts"
"""Liaison → `async (party) -> ContactBook`: who to ask, and where."""

RESOLVED = "hitl_resolved"
"""Liaison → `async (HumanTask) -> None`: resume the work waiting on an answer."""

CLOCK = "clock"
"""Liaison → `() -> datetime`: current time; tests move it forward."""

PUBLISH = "inapp_publish"
"""In-app channel → `async (party, event) -> None`: push to the party's open screens."""
