# CLAUDE.md — VendorLink

VendorLink is a configurable multi-agent layer that lets small vendors sell to retailers by voice, in their own language, within rules they set. It is the AI Builder Cup 2026 (Retail & Commerce) entry. **Prototype due 18 Oct 2026.**

Full design: `docs/solution_approach_v1.docx` (the why) and `docs/PLAN.md` (the coding plan: modules, data model, schedule). Read `docs/PLAN.md` before starting any new module.

## Non-negotiable rules

1. **Vendor side and retailer side never call each other directly.** Every cross-tenant message goes `POST trust-gateway/relay/{agent_id}/{skill}`. Never add a direct HTTP call between `vendor_svc` and `retailer_svc`.
2. **The Concierge cannot sign.** It has no signing tool. Only the Guardian signs, and only (a) when the decision is green, or (b) with a `ConsentRecord` whose `subject_hash == sha256(canonical_json(final_proposal))`. **Vendor consent is given only in-app or by voice, after the read-back, from her own words** (never from model-supplied text, never by Slack or email; those channels may only *nudge* her back to the app).
3. **The Guardian is deterministic.** No LLM calls anywhere in `agents/core/guardian/`. An LLM may only *word* an explanation, and that happens in the Concierge.
4. **Configuration, not code.** Business type, retailer type, country, language, thresholds and which specialists load all come from `config/`. If you are about to write `if business_type == "fresh_produce"` or `if country == "IN"`, stop: put it in a config file instead.
5. **Agents are called only by capability via `AgentRegistry.find()` / `invoke()` / `call()`** (`agents/registry.py`). Never import an agent from a service, from the Orchestrator or from another specialist.
6. **Every state change writes exactly one `AuditRecord`** (actor, on_behalf_of, action, payload_hash, config_hash) through the gateway's audit API. The audit log is append-only; never update or delete records.
7. **Deny by default** in the gateway. A new skill or scope needs an explicit entry in the policy table.
8. **Every GCP dependency stays behind its `vl_core` interface** with a local fallback. Never call Firestore, KMS, GCS or Model Armor directly from service code.
9. **No secrets in code or config.** Use env vars (`.env`, never committed) or Secret Manager. Keep `.env.example` current.

## Architecture at a glance

| Deployable | Path | Contains |
|---|---|---|
| `vendor-svc` | `services/vendor_svc/` | hosts the Guardian and Concierge agents; voice WebSocket bridge, vendor REST API, A2A server |
| `retailer-svc` | `services/retailer_svc/` | hosts the Orchestrator and specialist agents; orders, approval queue, config studio, retailer REST API, A2A server |
| `trust-gateway` | `services/trust_gateway/` | tokens, request-signature check, policy, screening, mandate store, hash-chained audit, containment; **agent control plane** (agent registry, event stream, control API, observability, scores) |
| `mocks` | `services/mocks/` | simulated ID / bank / FSSAI / P-IRT registries, demand feed, receiving |
| agents | `agents/` | `core/` (Guardian, Concierge on Google ADK, Orchestrator, Human Liaison), `channels/` (Slack and Gmail over MCP, in-app), `specialists/`, `registry.py`, standalone `host.py`, CLI. Configured by `config/agents.json`; see `agents/README.md` |
| shared lib | `packages/vl_core/` | schemas, config resolver, capability matching, crypto, store, llm, a2a, obs |
| web | `web/` | React + Vite + TS; two installable apps: `/vendor` (chat-first: Chat + "Needs you" is home, products are added in the chat; side nav on a laptop, tabs on a phone; ADR 0010) and `/retailer` (console: one Inbox for every decision, shown with its product, order or supplier; ADR 0011) |

## Repository layout

```
packages/vl_core/{schemas,config,registry,crypto,store,llm,a2a,obs}/
agents/{core/{guardian,concierge,orchestrator,liaison},channels/{slack,gmail,inapp},specialists/<name>}/   # agent.json + agent.py (+ prompts.py) each
services/{vendor_svc,retailer_svc,trust_gateway,mocks}/
config/agents.json   config/{business_profiles,retailer_profiles,country_packs,retailer_policies,workflows,presets,i18n,prompts,channels,hitl,contacts}/
seed/  web/  scripts/  infra/  tests/{unit,contract,e2e,attack,evals}/  docs/
```

## Commands

```bash
uv sync                              # install the Python workspace
make dev                             # all 4 services + web locally (in-memory store, local signer)
make check                           # ruff + mypy + pytest, no model calls (run before every commit)
make evals                           # specialist evals; real models when set in .env
uv run pytest tests/unit -q          # fast unit tests
uv run python scripts/e2e_order_loop.py     # order loop: green / amber / red, text only
uv run python scripts/attack_impostor.py    # must be BLOCKED
uv run python scripts/attack_injected_doc.py
uv run python scripts/api_check.py         # every route of every service; must pass (in-process)
uv run python scripts/ui_check.py          # every screen in headless Chrome (needs make dev)
uv run python -m agents mcp-tools SLACK     # check a configured MCP server's tools vs config/channels/
curl -X POST localhost:8002/dev/tick       # one Liaison pass now (replies, reminders, escalations)
uv run python scripts/demo_reset.py         # reset state + reseed configs A and B
uv run python scripts/demo_reset.py --scene waiting|sara   # start a demo scene (docs/DEMO.md)
uv run python scripts/export_schemas.py     # Pydantic → JSON Schema for web + config validation
uv run python -m agents list|validate|run <id>|invoke <id> <cap> <json>   # agents on their own
cd web && npm run dev
```

Local ports: vendor-svc 8001 · retailer-svc 8002 · trust-gateway 8000 · mocks 8009 · web 5173.

## Environment flags

`VL_STORE=memory|firestore` · `VL_BLOB=local|gcs` · `VL_SIGNER=local|kms` · `VL_SCREEN=heuristic|model_armor` · `LIVE_MODEL`, `PRO_MODEL`, `FLASH_MODEL`, `IMAGE_MODEL` (never hardcode model names) · `VL_TRACE=off|console|cloud` · `VL_OPERATOR_TOKEN` (control API) · `VL_CHANNELS=local|mcp`, `MCP_<SERVER>_*` (Slack/Gmail MCP servers) · `VL_HITL_TICK_SECONDS` · `VL_CHECKPOINTS=memory|sqlite|postgres`, `VL_CHECKPOINT_DSN` · `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` · `ACTIVE_CONFIG` (`A` or `B` for seeding).

## Code conventions

- **Python 3.12**, `uv` workspace, Pydantic v2, FastAPI, async throughout. Type hints on everything; `mypy --strict` on `vl_core`.
- **Every payload that crosses a service boundary is a `vl_core.schemas` model.** No bare dicts across boundaries. Add new entities to `vl_core/schemas/` first, then run `export_schemas.py`.
- **Hashing and signing:** always go through `vl_core.crypto.canonical_json()` (sorted keys, no whitespace, UTF-8). Never `json.dumps` something you are about to hash.
- **LLM calls:** structured outputs only through `vl_core.llm.Llm.generate_json(prompt, schema, images)` (schema-validated, one retry, "needs review" on failure, never a silent default). Conversation (text and Live voice) runs on **Google ADK** behind `vl_core.llm.ToolChat` / `LiveBridge` (`vl_core/llm/adk.py`); its tools are our tool layer, so every rule above still applies.
- **Human in the loop:** an agent that needs a person returns `InfoRequest`s (or a job calls `interrupt()`); the **Liaison** (`human.ask`) routes the question per `config/hitl/routing.json` to the role's contacts (`config/contacts/`), on in-app / Slack / Gmail, reminds, escalates and expires; answers resume the work. Never message people directly from a service or agent: go through the Liaison and the channel agents.
- **Durable jobs:** multi-step batch work that may wait on people is a LangGraph graph (`retailer_svc/jobs.py`) with a checkpointer from `vl_core.checkpoints`; side effects before an `interrupt()` must be idempotent.
- **Prompts**: an agent's task prompts live in its own `prompts.py`, one named `string.Template` constant per prompt (filled with `.substitute()`, never f-strings or `.format()`; vendor text is passed as a value, never pasted into the template). Per-language conversation prompts (the Concierge) stay in `config/prompts/<agent>.<lang>.md`.
- **Guardian reason codes** are machine codes (`QTY_OVER_DAILY_MAX`, `PICKUP_BEFORE_WINDOW`, `PRICE_BELOW_FLOOR`, `BUYER_NOT_APPROVED`, `PAYMENT_TOO_LATE`, `VALUE_OVER_AUTO_ACCEPT`). Language comes later, in the Concierge.
- **Orders are versioned.** A counter creates `version + 1`; never mutate an existing version.
- **Decision colours** are `green` (within rules, handled), `amber` (needs vendor OK), `red` (outside rules → counter), and `decline`. Use these exact strings in the API and UI.
- Logging: `log = vl_core.obs.get_logger("area")`, then `log.info("what happened", key=value, ...)`: one line per event; keyword arguments become fields, and service, party and `trace_id` are added automatically. Log every state change, decision, agent/model call and message to a person at INFO; routine reads at DEBUG; refusals at WARNING; failures with `log.exception`. Never log raw documents, audio, chat text or ID numbers (ids, codes, counts, hashes only). Pretty on a terminal, JSON on Cloud Run (`VL_LOG_FORMAT`).
- Web: TypeScript strict, function components, types generated from exported JSON Schema; all user-facing strings come from the Country Pack / i18n files.

## Adding things

**A new specialist** (details in `agents/README.md`)
1. `uv run python -m agents new <id> --capability <cap> --input <Model> --output <Model>` creates `agents/specialists/<name>/` (`agent.json` card + `agent.py` with `class X(Agent)` and one `@skill` per capability) and registers it disabled in `config/agents.json`. If it uses an LLM, pass `--llm` to get a `prompts.py`. Use `self.ctx` (llm, http, blob, repo), never module globals.
2. Enable it in `config/agents.json`, then reference it from a Business or Retailer Profile or a workflow step.
3. Add an eval set (around 10 cases) in `tests/evals/<name>/`; `tests/contract/` checks every registered agent automatically.
4. `uv run python -m agents validate`. Orchestrator and core agents must not change. If they need to, the design is wrong.
5. To run it as its own service: `python -m agents run <id>` and set `transport: "a2a"` + `endpoint` in `config/agents.json`. No code change.

**A new business type / country / retailer type:** config files only (see `docs/PLAN.md` §4). Validate with `make check`.

**A new gateway skill:** add a scope to the policy table, add an audit action name, and add a negative test proving an unauthorised caller is blocked (`tests/unit/gateway/test_policy.py` already checks every skill's reverse direction).

**A new route:** `scripts/api_check.py` fails on any route nothing exercises; add a step to its journey.

## Testing expectations

- Guardian: unit tests for each reason code, plus Hypothesis property tests showing it **never signs outside the mandate without matching consent**.
- Audit chain: tampering with any record makes `/audit/verify` report that seq.
- `e2e_order_loop.py` and both attack scripts must pass before merging anything that touches the gateway, Guardian or orchestrator.
- Specialists: contract test (schema in/out) plus eval set ≥ 8/10.

## Demo anchors (keep seed data consistent)

- **Config A:** handmade gifts (candles, home décor) · India (Telugu, English, INR; no FSSAI, it is not food) · gifting buyer → Craft Quality Checker + Ordering (`order.create`, the Replenishment Planner's one-off skill).
- **Config B:** packaged food · Indonesia (Bahasa Indonesia, IDR, P-IRT, halal) · grocery chain → Label & Food-Safety Checker + Supplier Compliance Auditor + Replenishment (`order.replenish`).
- **Maya** (hand-poured candles, Hyderabad, Telugu): mandate lavender candle ≥ ₹180/piece, ≤ 40/day, pickup ≥ 10:00, paid ≤ 15 days, auto-accept ≤ ₹5,000. Listed at ₹220.
- **Hero order** (corporate gifting desk, `store:hub-4`): 50 candles @ 08:00, ₹220, paid in 45 days → Guardian **red** with exactly `QTY_OVER_DAILY_MAX`, `PICKUP_BEFORE_WINDOW`, `PAYMENT_TOO_LATE` → counter **40 @ 10:00, 15 days** → value changes 20% > `auto_accept_counter_max_delta` 0.10 → approval queue → Human Liaison asks the buyer on Slack → approved → Maya says "yes" → signed Order Mandate. Gifting term template 1 is net-45, so every config A order starts there; `use_terms()` in `scripts/_demo.py` switches to net-15 for green/amber.
- **Listing enrichment** (config A, ADR 0008): a cluttered candle photo → Catalogue Image Checker flags it → Image Enhancer makes an auto-enhanced version and 2 AI versions of *her* photo (`candidates` in the retailer profile), each staged in its own look (`looks`: clean studio, warm wood, …; ADR 0012) (`IMAGE_MODEL`; retake tips without it) → Listing Enricher writes copy from her facts, each fact tagged with its source → her Guardian (`guardian.review_listing`, deterministic) approves within `listing_rules` or she picks the photo in the app → the retailer re-checks her chosen photo → AI photo → category manager approves on Slack → published, her side told (`listing.status`) → Console → Catalogue (ADR 0009).
- **Sara:** label missing the P-IRT number → flagged by the Label Checker.
- All vendors, retailers and registries are synthetic or simulated; the UI says so (a "Demo data" tag in each app header, the full sentence on hover).

## Scope guardrails

Channels in the MVP: in-app, voice, **Slack and Gmail (via MCP, for retailer staff/ops and as nudges to vendors)**. Not in the MVP: payments, logistics/routing, real registry integrations, WhatsApp or phone channels, configs C and D, sourcing assistant, analytics. If behind schedule, cut in this order: Quality & Disputes → Pricing Advisor → Gmail reply reading (keep sending) → reminder digests → review queue polish → voice for rules. **Never cut:** order loop, Guardian, gateway blocking, A→B switch.

## Working style for Claude

- Design before code: for anything that touches more than one module, propose the interface change first and wait for a go-ahead.
- Keep changes small and module-scoped, one module per PR, tests included.
- Prefer config over code, and deterministic code over LLM calls wherever correctness matters.
- When something in this file conflicts with `docs/PLAN.md`, this file wins; flag the conflict so the plan can be updated.