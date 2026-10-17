# VendorLink

**Small vendors sell to retailers in their own language, within rules they set.**

VendorLink is a configurable multi-agent layer between small vendors (farmers, home food makers, micro-brands) and the retailers who buy from them. A vendor sets her rules once, such as how much she can supply per day, her lowest price, her earliest pickup time and how fast she must be paid. Agents then handle orders, onboarding and compliance for her. Anything inside her rules is handled automatically; anything outside them is countered or brought back to her to decide.

This is our entry for the **AI Builder Cup 2026 (Retail & Commerce)**. All vendors, retailers and registries in this repo are synthetic or simulated.

---

## Why it is different

- **A deterministic Guardian is the only signer.** The Guardian checks every order against the vendor's mandate with plain code, not an LLM. It returns `green` (within rules), `amber` (needs her OK), `red` (outside rules, so it counters) or `decline`. The conversational agent (the Concierge) cannot sign anything.
- **Consent comes from her own words.** Anything outside the mandate needs the vendor's own "yes", given in the app or by voice after a read-back. Consent is never taken from model-generated text, Slack or email.
- **A trust gateway sits between the two sides.** Vendor and retailer services never call each other. Every cross-tenant message goes through a deny-by-default gateway that checks a signed request, a policy table and content screening, then writes a hash-chained audit record.
- **Configuration, not code.** Business type, retailer type, country, language, thresholds and which specialist agents load all come from JSON in `config/`. Switching from config A to config B changes the whole product without a code change:

  | | Config A | Config B |
  |---|---|---|
  | Business | Handmade gifts (candles, home décor) | Packaged food |
  | Country | India (INR) | Indonesia (P-IRT, halal, IDR) |
  | Languages | English, Telugu | English, Bahasa Indonesia |
  | Retailer | Gifting buyer | Grocery chain |
  | Specialists | Craft Quality Checker, Ordering (`order.create`) | Label & Food-Safety Checker, Supplier Compliance Auditor |

- **People stay in the loop.** When an agent needs a person, the Human Liaison sends the question to the right role in the app, on Slack or by Gmail (over MCP), then sends reminders, escalates and expires the request as configured.
- **Text first, voice optional.** The vendor app opens in English with a chat assistant. Voice and the local languages are one tap away.

## The demo story

1. **Maya** pours candles by hand at home in Hyderabad. Her mandate: at least ₹180 a candle, at most 40 a day, pickup from 10:00, payment within 15 days.
2. FreshCart's **corporate gifting desk** orders 50 candles for pickup at 08:00, paid in 45 days. The Guardian returns **red** (too many, too early, paid too late) and counters with **40 candles at 10:00, paid in 15 days**.
3. The counter changes the order by more than FreshCart's policy allows, so the Human Liaison asks the gifting buyer on Slack. The buyer approves. Maya hears the read-back and says "yes", and the Guardian signs the order mandate.
4. **Sara** onboards a packaged snack under config B. Her label is missing the P-IRT number, and the Label Checker flags it.
5. **Sam** tries to join with an expired licence and an injected document. The gateway blocks both attempts.

The scene-by-scene script is in [docs/DEMO.md](docs/DEMO.md).

## Architecture

| Deployable | Port | What it hosts |
|---|---|---|
| `trust-gateway` | 8000 | Tokens, request signatures, policy, screening, mandate store, hash-chained audit, containment, and the agent control plane (registry, events, observability, scores) |
| `vendor-svc` | 8001 | The Guardian and Concierge agents; vendor REST API, voice WebSocket bridge, A2A server |
| `retailer-svc` | 8002 | The Orchestrator, specialist agents and Human Liaison; orders, approval queue, config studio, retailer REST API |
| `mocks` | 8009 | Simulated ID, bank, FSSAI and P-IRT registries, demand feed, receiving |
| `web` | 5173 | Two installable apps: `/vendor` (chat-first: Chat with Needs you is home; Deals, Products, My shop, Activity) and `/retailer` (operations console: an Inbox of decisions with full context; observability under Admin) |

**Agents (17).** These are found and called by capability through `AgentRegistry`, never imported directly. The list and settings are in `config/agents.json`.

- Core: Guardian, Concierge (Google ADK), Orchestrator, Human Liaison
- Channels: in-app, Slack (MCP), Gmail (MCP)
- Specialists: Catalogue Builder, Craft Quality Checker, Catalogue Image Checker, Image Enhancer, Listing Enricher, Produce Grader (spare, in no preset), Label Checker, Onboarding Verifier, Compliance Auditor, Replenishment Planner, Terms Agent

Eight of the agents call an AI model: the Concierge, Catalogue Builder, Craft Quality Checker, Catalogue Image Checker, Image Enhancer (plus an image model), Listing Enricher, Produce Grader and Label Checker. Every decision that matters (signing, approving on her behalf, policy, blocking, audit) is deterministic code.

**Listing enrichment.** When a vendor's photo or description misses the retailer's catalogue standards (`catalogue_standards` in the retailer profile), the retailer's agents prepare an auto-enhanced version and AI-regenerated versions (two in config A, set by `catalogue_standards.image.candidates`) of *her own* photo and catalogue copy written from *her own* facts, each fact tagged with where it came from. Her Guardian approves it on her behalf when it stays within her listing rules; otherwise she picks the photo in the app. The retailer re-checks whichever photo she picks, then publishes, or a category manager reviews on Slack; her side is told either way. Published products appear in the console's **Catalogue** with their photos, copy, history and orders. See [ADR 0008](docs/adr/0008-listing-enrichment.md) and [ADR 0009](docs/adr/0009-catalogue-and-photo-pipeline.md).

**Shared library.** `packages/vl_core` holds the schemas, config resolver, capability matching, crypto, storage, LLM wrapper, A2A client, checkpoints and observability. Every cloud dependency sits behind a `vl_core` interface with a local fallback:

| Concern | Local (default) | Google Cloud |
|---|---|---|
| Store | `memory` / `sqlite` | Firestore |
| Files | local disk | Cloud Storage |
| Signing | local Ed25519 keys | Cloud KMS |
| Screening | heuristics | Model Armor |

## Quick start

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
make install            # uv sync + npm install
cp .env.example .env    # runs with no keys; add a Gemini key for the AI parts
make dev                # 4 services + web, seeded with config A
```

Then open:

- Vendor app: http://localhost:5173/vendor
- Retailer console: http://localhost:5173/retailer

The whole loop runs without any keys. Rules-based paths work offline, and the AI steps return "needs review". To get Gemini, Slack or Gmail keys and run everything end to end, follow **[docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md)**.

Useful while the stack is running:

```bash
uv run python scripts/demo_reset.py                 # reset state and reseed
uv run python scripts/demo_reset.py --scene waiting # start a demo scene (waiting | sara)
curl -X POST localhost:8002/dev/tick                # run one Human Liaison pass now
```

## Checks

| Command | What it proves |
|---|---|
| `make check` | ruff, mypy (strict on `vl_core`) and the test suite; never calls a model, even with keys in `.env` |
| `make e2e` | The order loop: green, amber and red, ending in a signed mandate |
| `make attacks` | The impostor and the injected document are both **BLOCKED** |
| `make api-check` | Every route of every service is exercised (in-process) |
| `make ui-check` | Every screen renders in headless Chrome, on phone and laptop sizes (needs `make dev`) |
| `make evals` | Specialist eval sets (each must score at least 8/10); uses the models in `.env` when set |
| `make web-check` | TypeScript type check and production build of the web app |

CI (`.github/workflows/ci.yml`) runs `check`, `evals`, `api-check`, `e2e`, `attacks` and `web-check` on every push, plus agent validation and a check that the generated web types are up to date.

## Repository layout

```
agents/          core/ (guardian, concierge, orchestrator, liaison), channels/, specialists/, registry.py, CLI
packages/vl_core schemas, config, registry, crypto, store, llm, a2a, obs
services/        trust_gateway/, vendor_svc/, retailer_svc/, mocks/
config/          agents.json, business & retailer profiles, country packs, policies, workflows,
                 presets (A/B), i18n, prompts, channels, hitl, contacts, scoring
web/             React + Vite + TypeScript (vendor app and retailer console)
seed/            synthetic vendors, retailer, documents and attack fixtures
scripts/         dev stack, demo reset and scenes, e2e, attacks, api and ui checks, schema export
tests/           unit/, contract/, e2e/, attack/, evals/
infra/           Cloud Run / Cloud Build deploy scripts and IAM notes
docs/            plan, ADRs, local setup, demo script, deploy guide
```

## Documentation

| Doc | Read it for |
|---|---|
| [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) | Getting keys, filling `.env`, running and testing locally |
| [docs/DEMO.md](docs/DEMO.md) | Recording setup and the demo scenes |
| [docs/PLAN.md](docs/PLAN.md) | Modules, data model and build plan |
| [docs/adr/](docs/adr/) | Architecture decisions (interfaces, agents package, control plane, human in the loop, cloud adapters, logging, handmade-gifts pivot, listing enrichment, catalogue) |
| [agents/README.md](agents/README.md) | How agents work and how to add a specialist |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Deploying to Google Cloud Run (optional) |
| [CLAUDE.md](CLAUDE.md) | Project rules and conventions for contributors and coding agents |

## Extending it

- **New business type, country or retailer type:** add config files only (see `docs/PLAN.md` §4), then run `make check`.
- **New specialist agent:** run `uv run python -m agents new <id> --capability <cap> --input <Model> --output <Model>`, enable it in `config/agents.json` and add an eval set. The Orchestrator and core agents do not change.
- **New gateway skill:** add a policy entry and an audit action, plus a negative test showing an unauthorised caller is blocked.

## Status and limits

This is a prototype built for a competition, and some parts are deliberately limited:

- Registries, payments and logistics are simulated. The UI says so.
- The Gmail channel only sends. Reading replies is on the cut list, so replies come through the app or Slack.
- Cloud adapters (Firestore, Cloud Storage, KMS, Model Armor) are implemented and unit-tested, but the demo runs locally.
- Not in scope: WhatsApp or phone channels, real registry integrations, payments, routing, analytics.
