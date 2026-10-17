"""VendorLink agents. Each agent is a folder with `agent.json` (its card) and `agent.py`.

- `agents.core`: Guardian, Concierge, Orchestrator (the system's backbone).
- `agents.specialists`: capability providers the Orchestrator finds by capability.
- `agents.registry.AgentRegistry`: discovers agents from `config/agents.json` and invokes them
  in-process or over A2A. `agents.host`: runs any one agent on its own.
"""

from agents.base import Agent, AgentContext, AgentError, skill
from agents.registry import AgentRegistry

__all__ = ["Agent", "AgentContext", "AgentError", "AgentRegistry", "skill"]
