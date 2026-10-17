# VendorLink architecture

How VendorLink is built and why. For setup and running, see the [README](../README.md); for the
detailed module plan, [PLAN.md](PLAN.md); for individual decisions, [adr/](adr/).

## Design principles

- **A deterministic Guardian is the only signer.** The Guardian checks every order against the
  vendor's mandate with plain code, not an LLM. It returns `green` (within rules), `amber` (needs
  her OK), `red` (outside rules, so it counters) or `decline`. The conversational agent (the
  Concierge) cannot sign anything.
- **Consent comes from her own words.** Anything outside the mandate needs the vendor's own
  "yes", given in the app or by voice after a read-back. Consent is never taken from
  model-generated text, Slack or email.
- **A trust gateway sits between the two sides.** Vendor and retailer services never call each
  other. Every cross-tenant message goes through a deny-by-default gateway that checks a signed
  request, a policy table and content screening, then writes a hash-chained audit record.
- **Configuration, not code.** Business type, retailer type, country, language, thresholds and
  which specialist agents load all come from JSON in `config/`. Switching from config A to
  config B changes the whole product without a code change:

  | | Config A | Config B |
  |---|---|---|
  | Business | Handmade gifts (candles, home décor) | Packaged food |
  | Country | India (INR) | Indonesia (P-IRT, halal, IDR) |
  | Languages | English, Telugu | English, Bahasa Indonesia |
  | Retailer | Gifting buyer | Grocery chain |
  | Specialists | Craft Quality Checker, Ordering (`order.create`) | Label & Food-Safety Checker, Supplier Compliance Auditor |

- **People stay in the loop.** When an agent needs a person, the Human Liaison sends the
  question to the right role in the app, on Slack or by Gmail (over MCP), then sends reminders,
  escalates and expires the request as configured.
- **Text first, voice optional.** The vendor app opens in English with a chat assistant. Voice
  and the local languages are one tap away.

## Services

| Deployable | Port | What it hosts |
|---|---|---|
| `trust-gateway` | 8000 | Tokens, request signatures, policy, screening, mandate store, hash-chained audit, containment, and the agent control plane (registry, events, observability, scores) |
| `vendor-svc` | 8001 | The Guardian and Concierge agents; vendor REST API, voice WebSocket bridge, A2A server |
| `retailer-svc` | 8002 | The Orchestrator, specialist agents and Human Liaison; orders, approval queue, config studio, retailer REST API |
| `mocks` | 8009 | Simulated ID, bank, FSSAI and P-IRT registries, demand feed, receiving |
| `web` | 5173 | Two installable apps: `/vendor` (chat-first: Chat with Needs you is home; Deals, Products, My shop, Activity) and `/retailer` (operations console: an Inbox of decisions with full context; observability under Admin) |

## Agents

There are 17 agents. They are found and called by capability through `AgentRegistry`, never
imported directly. The list and settings are in `config/agents.json`; how to write one is in
[agents/README.md](../agents/README.md).

- **Core:** Guardian, Concierge (Google ADK), Orchestrator, Human Liaison
- **Channels:** in-app, Slack (MCP), Gmail (MCP)
- **Specialists:** Catalogue Builder, Craft Quality Checker, Catalogue Image Checker, Image
  Enhancer, Listing Enricher, Produce Grader (spare, in no preset), Label Checker, Onboarding
  Verifier, Compliance Auditor, Replenishment Planner, Terms Agent

Eight agents call an AI model: the Concierge, Catalogue Builder, Craft Quality Checker,
Catalogue Image Checker, Image Enhancer (plus an image model), Listing Enricher, Produce Grader
and Label Checker. Every decision that matters (signing, approving on her behalf, policy,
blocking, audit) is deterministic code.

## Listing enrichment

When a vendor's photo or description misses the retailer's catalogue standards
(`catalogue_standards` in the retailer profile), the retailer's agents prepare an auto-enhanced
version and AI-regenerated versions (two in config A, set by
`catalogue_standards.image.candidates`) of *her own* photo, plus catalogue copy written from
*her own* facts, each fact tagged with where it came from.

Her Guardian approves the result on her behalf when it stays within her listing rules;
otherwise she picks the photo in the app. The retailer re-checks whichever photo she picks, then
publishes it, or a category manager reviews it on Slack; her side is told either way. Published
products appear in the console's **Catalogue** with their photos, copy, history and orders.
See [ADR 0008](adr/0008-listing-enrichment.md), [ADR 0009](adr/0009-catalogue-and-photo-pipeline.md)
and [ADR 0012](adr/0012-styled-looks-for-ai-photos.md).

## Shared library and cloud adapters

`packages/vl_core` holds the schemas, config resolver, capability matching, crypto, storage, LLM
wrapper, A2A client, checkpoints and observability. Every cloud dependency sits behind a
`vl_core` interface with a local fallback:

| Concern | Local (default) | Google Cloud |
|---|---|---|
| Store | `memory` / `sqlite` | Firestore |
| Files | local disk | Cloud Storage |
| Signing | local Ed25519 keys | Cloud KMS |
| Screening | heuristics | Model Armor |

Deploying to Cloud Run is described in [DEPLOY.md](DEPLOY.md).

## Extending it

- **New business type, country or retailer type:** add config files only (see
  [PLAN.md](PLAN.md) §4), then run `make check`.
- **New specialist agent:** run
  `uv run python -m agents new <id> --capability <cap> --input <Model> --output <Model>`, enable
  it in `config/agents.json` and add an eval set. The Orchestrator and core agents do not change.
- **New gateway skill:** add a policy entry and an audit action, plus a negative test showing an
  unauthorised caller is blocked.

The full set of project rules is in [CLAUDE.md](../CLAUDE.md).
