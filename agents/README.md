# Agents

Every VendorLink agent is a folder with two files:

```
agents/
├─ base.py            Agent base class, @skill, AgentContext (what an agent may use)
├─ registry.py        AgentRegistry: reads config/agents.json, finds agents by capability,
│                     invokes them in-process or over A2A
├─ host.py            runs any one agent as its own A2A service
├─ __main__.py        CLI: list · validate · run · invoke · new
├─ core/              the backbone
│  ├─ guardian/       vendor tenant · decides, counters, the only signer · no LLM
│  ├─ concierge/      vendor tenant · Google ADK (text + Live) · reads back, asks consent · cannot sign
│  ├─ orchestrator/   retailer tenant · runs workflows, applies Retailer Policy
│  └─ liaison/        any tenant · asks people, reminds, escalates, expires, resumes work
├─ channels/          how agents reach people
│  ├─ slack/          Slack MCP server (MCP_SLACK_*) or the local outbox
│  ├─ gmail/          Gmail MCP server (MCP_GMAIL_*) or the local outbox
│  └─ inapp/          vendor app / console
└─ specialists/       capability providers, found by capability only
   ├─ catalogue_builder/      catalogue.build
   ├─ produce_grader/         grade.produce        (config A)
   ├─ label_checker/          grade.label          (config B)
   ├─ compliance_auditor/     compliance.check     (config B)
   ├─ onboarding_verifier/    vendor.verify
   ├─ terms_agent/            terms.propose
   └─ replenishment_planner/  order.replenish, order.create   (config A)
```

Each agent folder:

| File | What |
|---|---|
| `agent.json` | the card: id, kind (`core`/`specialist`), tenant, skills (capability + input/output model names from `vl_core.schemas`), permissions, entrypoint |
| `agent.py` | `class X(Agent)` with one `@skill("capability")` async method per skill, typed `(self, inp: In) -> Out` |
| `prompts.py` | only if the agent uses an LLM: one named `string.Template` constant per prompt (e.g. `GRADE_PHOTO`), filled with `.substitute(...)` |

The card and the code are checked against each other at load time. A skill whose types differ
from the card, or a card skill with no method, is an error.

## The config file: `config/agents.json`

```json
{"id": "produce-grader", "card": "agents/specialists/produce_grader/agent.json",
 "hosts": ["retailer-svc"], "settings": {"llm_tier": "flash"}}
```

| Field | Meaning |
|---|---|
| `card` | path to the agent's `agent.json` (repo-relative) |
| `enabled` | `false` hides the agent everywhere |
| `hosts` | deployables that run it **in-process** (`vendor-svc`, `retailer-svc`) |
| `transport` / `endpoint` | `"a2a"` + URL to run it as its own service; `${ENV:-default}` allowed |
| `settings` | the agent's own options, validated against its `Options` model |

Which **specialists** a retailer actually uses still comes from its Business and Retailer
Profiles (`ActiveConfig.specialists`). `agents.json` says what exists and where it runs.

## Working on its own and as part of the system

**On its own**, every agent can be run and called without the rest of VendorLink:

```bash
uv run python -m agents list
uv run python -m agents run label-checker --port 8101     # GET /.well-known/agent.json
curl -X POST localhost:8101/invoke/grade.label -d @step.json
uv run python -m agents invoke terms-agent terms.propose input.json
```

**In the system**, hosts call agents only through the registry, by capability:

```python
registry = AgentRegistry.load(settings, host="retailer-svc", signer=signer)
out = await registry.call("orchestrator.build_listing", payload, BuildListingOut)
```

The Orchestrator finds specialists the same way (`registry.find("grade.*", active_config)`),
so the same code calls a specialist in-process or on another machine. Only `agents.json` decides.

To **split a specialist into its own service**, run `python -m agents run <id>` there and set
`"transport": "a2a", "endpoint": "${GRADER_URL}"` in its entry. No code changes.

## Rules the registry enforces

- **Tenancy**: a `vendor` agent cannot be hosted by `retailer-svc` (and vice versa).
- **Signing**: only cards with the `sign` permission get a signer in their context. Today that is
  the Guardian alone.
- **Internal skills** (Guardian signing) can be called only in-process. A standalone host never
  exposes them, and the registry refuses them over A2A.
- **Contracts**: inputs and outputs are validated against the card's models on every call, local
  or remote.

## Adding an agent

```bash
uv run python -m agents new pricing-advisor --capability price.advise \
    --input ListingStepIn --output ListingStepOut --description "Suggest a price" --llm
```

This creates `agents/specialists/pricing_advisor/` (card, stub class and, with `--llm`,
`prompts.py`) and registers it
**disabled** in `config/agents.json`. Then:

1. Implement `agent.py` (use `self.ctx.llm(...)`, `self.ctx.http(...)`, `self.ctx.blob()`;
   never module-level globals). If it uses an LLM, pass `--llm` (or add `prompts.py` by hand)
   and keep every prompt there as a named `Template`.
2. Add a contract test and an eval set (`tests/evals/<name>/`, about 10 cases, ≥ 8/10).
3. Set `"enabled": true`, list the agent in a Business or Retailer Profile, or name its
   capability in a workflow (`config/workflows/`).
4. `uv run python -m agents validate` and `make check`.

The Orchestrator and the other agents do not change. If they need to, the design is wrong.

An **external** agent (someone else's A2A service) needs only a card file with `transport: "a2a"`
and an `endpoint`, plus an entry in `agents.json`. No code in this repo.

## Control plane

Every host (vendor-svc, retailer-svc) registers its in-process agents with the gateway through
`agents/control.py` (`HostControl`), reports each call and decision as an `AgentEvent`, and
checks a cached status snapshot before every in-process call. An operator can suspend,
quarantine or revoke any agent, including a specialist, from the retailer console's
Observability page, and the next call is refused (`agent_blocked`). See ADR 0003.

## Human in the loop

When an agent needs a person, it returns `InfoRequest`s (or a LangGraph job calls `interrupt()`)
and the **Liaison** takes over: `config/hitl/routing.json` decides who is asked, on which channel,
and when to remind, escalate or give up; `config/contacts/` says where each role is reached.
Answers from any channel resume the waiting work. Vendors are asked in-app (and the Concierge
reads questions out); Slack and Gmail are for retailer staff and ops, and only nudge vendors back
to the app. Consent is never taken from Slack, email or model-supplied text.

Connecting a real workspace: set `VL_CHANNELS=mcp` and `MCP_SLACK_*` / `MCP_GMAIL_*`, run
`python -m agents mcp-tools SLACK` and adjust `config/channels/slack.json` if the server's tool
names differ. See ADR 0004.
