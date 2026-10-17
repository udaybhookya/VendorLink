# ADR 0002 — Agents as a package: registry, core agents, specialists, one config file

Status: proposed (built, needs team review) · 13 Oct 2026

## Context
Agents were spread across services: specialists in `services/retailer_svc/specialists/`, Guardian
and Concierge inside `vendor_svc`, the Orchestrator mixed with retailer state. Cards were
duplicated in `config/agent_cards/`. No agent could run on its own, and a specialist was a bare
`handle()` function.

## Decision
1. **`agents/` is a top-level package**: `core/` (Guardian, Concierge, Orchestrator) and
   `specialists/`. Each agent is a folder with `agent.json` (its card, the only copy) and
   `agent.py` (`class X(Agent)` with `@skill` methods).
2. **`config/agents.json` registers and configures every agent**: card path, `enabled`, `hosts`
   (which deployable runs it in-process), optional `transport: "a2a"` + `endpoint` (with
   `${ENV}`), and `settings` validated against the agent's `Options` model.
   `config/agent_cards/` is gone.
3. **`agents/registry.py` (`AgentRegistry`)** replaces `vl_core.registry.Registry`. It resolves
   for each host whether an agent is `local`, `a2a` or unavailable; finds by capability (filtered
   by `ActiveConfig` for specialists); invokes with schema validation both ways.
   `vl_core.registry` keeps only `capability_matches`.
4. **Cards are skill-based** (`skills: [{capability, input_schema, output_schema, internal}]`),
   with `kind` (`core`/`specialist`) and `tenant` (`vendor`/`retailer`/`any`). Core agents need
   several skills with different contracts; one input/output pair per card could not express it.
5. **Agents get everything through `AgentContext`** (settings, llm, http, blob, repo, registry,
   resources). Only cards with the `sign` permission receive a signer: the Guardian.
6. **Standalone**: `python -m agents run <id>` serves any agent (`/.well-known/agent.json`,
   `/invoke/{capability}`). Skills marked `internal` are never exposed there or over A2A.
7. **Core agents are reshaped to fit**:
   - *Guardian* owns the vendor's mandates and daily commitments (PLAN M2 "keeps the vendor's
     rules"). Skills: `guardian.evaluate` (public, pure), and the internal `decide`,
     `sign_order`, `sign_mandate`, `install_mandate`, `mandate`. A mandate is put in force only
     after the gateway accepts it, and only if it carries this Guardian's own signature.
   - *Concierge* acts on vendor state only through a `VendorPort`: in-process in vendor-svc, or
     HTTP to the vendor REST API when standalone. It imports no Guardian, crypto or store code
     (checked by a contract test).
   - *Orchestrator* is stateless: workflow planning plus Retailer Policy gates, given an
     `ActiveConfig`. Orders, queue, signing and gateway calls moved to
     `services/retailer_svc/operations.py`.
8. **Prompts are code-adjacent constants**: each LLM agent has `prompts.py` with one named
   `string.Template` per prompt (an agent may need several: per task, per retry, per category).
   `.substitute()` fails loudly on a missing placeholder; vendor text is only ever a value.
   Contract tests check that every placeholder is filled and that no `prompt.md` remains. The
   Concierge's per-language conversation prompts stay in `config/prompts/`.
9. **Workflows name concrete capabilities** where an agent has several (`order.replenish`, not
   `order.*`). A wildcard must resolve to exactly one skill.

## Consequences
- Moving a specialist to its own service is a config change.
- An external A2A agent can be added with a card file plus a config entry; no code.
- `CLAUDE.md` rule 5, the layout and "Adding a specialist" were updated to match.
- Tenancy is enforced at load time: `retailer-svc` cannot host a vendor agent, and it sees the
  Guardian and Concierge as unavailable.
- Standalone agent endpoints have no authentication of their own yet. They are intra-tenant
  (the gateway still guards every cross-tenant call), but a split-out agent should sit behind
  Cloud Run IAM or a shared token before deploy.
