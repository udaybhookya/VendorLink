# VendorLink — Coding Plan v1

Based on *Solution Approach v1* (12 Oct 2026). Prototype due **18 Oct**.
Goal of this doc: fix the code architecture, the module boundaries, and what each module must do before we write code.

---

## 1. Guiding decisions

| # | Decision | Why |
|---|----------|-----|
| D1 | **Monorepo, Python 3.12 backend (uv workspace) + one TypeScript web app** | One place for schemas, configs and seed data; teammates can run everything with one command |
| D2 | **4 backend deployables**, not one per agent: `vendor-svc`, `retailer-svc`, `trust-gateway`, `mocks` | Keeps the "two tenants, one gateway" story real without 12 Cloud Run services to babysit in 6 days |
| D3 | **Specialists run in-process inside `retailer-svc`, but each one has an A2A Agent Card and is called only through the registry by capability** | Configurability is proven (load by card, not by import); any specialist can be split into its own service later by changing its transport from `local` to `a2a` |
| D4 | **Orchestrator = deterministic workflows per job type + LLM inside specialists** | Job types are known (onboard, list, terms, order, receive). Workflows make the demo repeatable; the LLM does the hard perception work (vision, extraction, language) |
| D5 | **Guardian is pure Python, no LLM in the decision path** | Principle 3: the agent that talks cannot approve. Fully unit-testable |
| D6 | **Every GCP dependency sits behind an interface with a local fallback** (Firestore ↔ in-memory, KMS ↔ local Ed25519, Model Armor ↔ heuristic screen, GCS ↔ local dir) | Local dev with no cloud; flip flags for deploy; resilience on demo day |
| D7 | **Build the order loop in text first, then add voice on top** | The order loop is the demo's spine and the riskiest integration. Voice is a channel onto the same tools |
| D8 | **All model names in env/config** (`LIVE_MODEL`, `PRO_MODEL`, `FLASH_MODEL`) | Swap models without code changes |

---

## 2. Solution architecture (code view)

```
                ┌──────────────────────────── web/ (React + Vite, Firebase Hosting) ───────────────────────────┐
                │   /vendor  — mobile PWA (push-to-talk, camera,       /retailer — console (studio, pipeline,  │
                │              rules card, deals inbox)                            review queue, orders, audit) │
                └──────────┬──────────────────────────────────────────────────────────────┬─────────────────────┘
                 WebSocket (audio) + REST                                               REST + SSE
                           │                                                              │
 ╔═════════ VENDOR TENANT (facilitator keys) ═════════╗          ╔═════════ RETAILER TENANT ════════════════════════╗
 ║  vendor-svc  (Cloud Run)                           ║          ║  retailer-svc  (Cloud Run)                       ║
 ║  ┌──────────────┐   tools   ┌───────────────────┐  ║          ║  ┌────────────────────┐   ┌───────────────────┐  ║
 ║  │ Concierge    │──────────▶│ Vendor tools API  │  ║          ║  │ Orchestrator       │──▶│ Registry client   │  ║
 ║  │ (ADK +       │           │ (profile, docs,   │  ║          ║  │ (workflows +policy)│   │ (find by capabil.)│  ║
 ║  │  Gemini Live)│           │  listings, rules) │  ║          ║  └────────────────────┘   └────────┬──────────┘  ║
 ║  └──────────────┘           └─────────┬─────────┘  ║          ║          specialists (in-process, each w/ card) ║
 ║  ┌──────────────┐  decide/sign        │            ║          ║  Onboarding·Catalogue·Terms·Ordering·Quality    ║
 ║  │ Guardian     │◀────────────────────┘            ║          ║  ProduceGrader·LabelChecker·Replenishment·      ║
 ║  │ (rules, no   │  A2A server: order.propose,      ║          ║  ComplianceAuditor                               ║
 ║  │  LLM)        │  terms.propose, quality.report   ║          ║                                                  ║
 ║  └──────┬───────┘                                  ║          ╚═══════════════════════╤══════════════════════════╝
 ╚═════════╪══════════════════════════════════════════╝                                  │
           │  A2A (signed request + scoped token)                                          │ A2A
           ▼                                                                               ▼
 ┌───────────────────────────────────── trust-gateway (Cloud Run) ──────────────────────────────────────────────┐
 │  identity & tokens │ policy check (Retailer Policy + Mandate coarse) │ screening (Model Armor) │ relay         │
 │  mandate store (verify sigs) │ hash-chained audit log │ containment (revoke, quarantine, alert)              │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
           │                                       │                                     │
   Firestore (state, config, registry)     Cloud KMS (sign keys)                GCS (photos, consent audio)
                                                                                         │
                                           mocks (Cloud Run): ID / bank / FSSAI / P-IRT registries, demand feed
```

**Rule:** `vendor-svc` and `retailer-svc` never call each other directly. Every cross-tenant message is `POST trust-gateway/relay/{agent_id}/{skill}`.

---

## 3. Repository layout

```
vendorlink/
├─ packages/
│  └─ vl_core/                 # shared library, imported by every service
│     ├─ schemas/              # Pydantic models (single source of truth → JSON Schema export)
│     ├─ config/               # loader, validator, ActiveConfig resolver
│     ├─ registry/             # Agent Card model, registry store, capability lookup, transports
│     ├─ crypto/               # canonical JSON, hashing, Signer (KMS | local), JWT helpers
│     ├─ store/                # Repo interfaces: Firestore | in-memory; BlobStore: GCS | local
│     ├─ llm/                  # thin Gemini client (text, vision, JSON-mode), prompt loader
│     ├─ a2a/                  # client/server helpers, envelope, request signing
│     └─ obs/                  # structured logging, trace ids
├─ services/
│  ├─ vendor_svc/              # concierge/, guardian/, voice/, api/, a2a_server.py
│  ├─ retailer_svc/            # orchestrator/, specialists/<name>/, api/, a2a_server.py
│  ├─ trust_gateway/           # identity/, policy/, screening/, mandates/, audit/, containment/, relay.py
│  └─ mocks/                   # registries.py, demand.py, fixtures/
├─ config/
│  ├─ business_profiles/       # handmade_gifts.json, packaged_food.json, fresh_produce.json (spare)
│  ├─ retailer_profiles/       # gifting.json, grocery_chain.json, quick_commerce.json (spare)
│  ├─ country_packs/           # IN.json, ID.json
│  ├─ retailer_policies/       # default_qc.json, default_grocery.json
│  ├─ agent_cards/             # one JSON per specialist + vendor agent
│  └─ prompts/                 # per agent, per language (concierge.te.md, concierge.id.md …)
├─ seed/                       # vendors, retailers, stores, demand, sample photos, attack inputs
├─ web/                        # React + Vite + TS; routes /vendor and /retailer
├─ scripts/                    # dev.sh, seed.py, demo_reset.py, attack_impostor.py, e2e_order_loop.py
├─ infra/                      # Dockerfiles, cloudbuild.yaml, deploy.sh, firestore.rules, iam.md
├─ tests/                      # unit/, contract/, e2e/, attack/, evals/
└─ docs/                       # this plan, ADRs, demo script
```

---

## 4. Core data model (`vl_core.schemas`)

| Entity | Key fields | Notes |
|--------|------------|-------|
| `BusinessProfile` | id, version, attributes[], units[], grading_rules, required_docs[], specialists[] | Config |
| `RetailerProfile` | id, version, retailer_type, ordering_mode, categories[], quality_thresholds, term_templates[], specialists[] | Config |
| `CountryPack` | code, languages[], currency, local_units, id_docs[], food_safety_regs[] (name + regex), consent_wording{lang} | Config |
| `RetailerPolicy` | auto_order_max_value, auto_accept_counter_max_delta, new_vendor_needs_human, listing_publish_min_confidence | Config |
| `ActiveConfig` | retailer_id, refs to the four above, resolved specialists[], `config_hash` | Resolved; hash goes into every audit record |
| `AgentCard` | agent_id, name, capabilities[], input_schema, output_schema, permissions[], transport (`local`/`a2a`), endpoint | Registry |
| `Vendor` | id, tenant, business_profile, country, language, status (draft→verifying→pending_approval→active), docs[], agent_id | |
| `AgentCredential` | agent_id, vendor_id, public_key, status (active/revoked), issued_at | Issued after approval |
| `Listing` | id, vendor_id, sku_name, category, unit, price, qty, grade, shelf_life_days, photos[], flags[], confidence, status | |
| `StandingMandate` | vendor_id, price_floor{sku/unit}, max_qty_per_day, pickup_windows[], payment_days_max, approved_buyers[], auto_accept{max_value}, signature, consent_ref | AP2 "intent" analogue |
| `OrderProposal` | order_id, version, retailer_id, store_id, lines[{sku, qty, unit, price}], pickup_at, payment_days, terms_version | Versioned; counters create v+1 |
| `Decision` | order_id, version, status `green/amber/red/decline`, counter?: OrderProposal, reasons[] (codes + params) | Guardian output |
| `ConsentRecord` | id, vendor_id, subject_hash, audio_uri, transcript, language, captured_at | Hash of exact terms |
| `OrderMandate` | order_id, final_version, terms_hash, vendor_sig, retailer_sig, consent_ref? | AP2 "cart" analogue |
| `AuditRecord` | seq, ts, actor_agent_id, on_behalf_of, action, target, payload_hash, config_hash, outcome, prev_hash, hash | Hash chain |
| `SecurityEvent` | id, type (bad_token, out_of_scope, injection, rate), agent_id, session_id, evidence, action_taken | |
| `ReceivingReport` / `Dispute` | order_id, received_qty, rejected_qty, reasons, photos[], timeline[] | Should |

Reason codes (`QTY_OVER_DAILY_MAX`, `PICKUP_BEFORE_WINDOW`, `PRICE_BELOW_FLOOR`, `BUYER_NOT_APPROVED`, `PAYMENT_TOO_LATE`, `VALUE_OVER_AUTO_ACCEPT`) are machine codes; the Concierge turns them into the vendor's language.

---

## 5. Module plans

Each module: **purpose → interfaces → internals → done when**. Track = suggested owner for a team of four (V = vendor & voice, R = retailer & specialists, T = trust & audit, U = UI, data & demo).

### M0 · `vl_core` — shared foundation  *(T, Day 1)*
- **Purpose:** schemas, config, registry, crypto and storage that every service shares.
- **Interfaces:**
  - `load_config(kind, id, version=None) -> Model`, `resolve_active_config(retailer_id) -> ActiveConfig`
  - `Registry.find(capability, active_config) -> AgentCard`, `Registry.invoke(card, payload) -> dict` (transport chosen from card)
  - `canonical_json(obj) -> bytes` (sorted keys, no whitespace), `sha256_hex(bytes)`
  - `Signer.sign(key_id, bytes)`, `Signer.verify(pub, bytes, sig)` — `KmsSigner` | `LocalSigner`
  - `Repo[T]` (get/put/query/watch) — `FirestoreRepo` | `MemoryRepo`; `BlobStore` — `GcsBlobStore` | `LocalBlobStore`
  - `Llm.generate_json(prompt, schema, images=[])` with schema-validated output and one retry
- **Internals:** Pydantic v2; `scripts/export_schemas.py` writes JSON Schema for the web app and config validation; env flags `VL_STORE=memory|firestore`, `VL_SIGNER=local|kms`, `VL_SCREEN=heuristic|model_armor`.
- **Done when:** all config files in `config/` validate in CI; `resolve_active_config` returns config A and B with different specialist sets and hashes.

### M1 · Trust Gateway  *(T, Day 1 skeleton → Day 4 full)*
- **Purpose:** the only path between tenants; identity, authorisation, screening, mandates, audit, containment.
- **Interfaces (REST):**
  - `POST /credentials` (on vendor approval) → registers agent public key
  - `POST /tokens` → short-lived JWT (5 min): `sub` agent_id, `obo` vendor/retailer id, `scope` one action, `aud` target agent, `jti`
  - `POST /relay/{agent_id}/{skill}` → verify token + request signature → scope & policy check → screen → audit → forward over A2A → audit response
  - `POST /mandates/standing`, `POST /mandates/order` → verify signatures, store, audit
  - `POST /screen` → for documents/images uploaded on the vendor side before any LLM sees them
  - `GET /audit?party=…`, `GET /audit/verify` → recompute chain, return first broken seq or OK
  - `GET /security-events`, `POST /agents/{id}/revoke`
- **Internals:**
  - *Request signing:* caller signs `canonical(method, path, body_hash, jti)` with its agent key; gateway verifies against the registered public key. An impostor has no key → `bad_signature`.
  - *Policy:* deny by default. Table `scope → allowed (from_type, to_type, skill)`; retailer-originated calls checked against Retailer Policy (e.g., auto order value cap); vendor mandate checked coarsely (buyer on allow list). Fine-grained accept/counter stays in the Guardian.
  - *Screening:* Model Armor on text and PDF/Office; spreadsheets → text first; images → hash duplicate check + EXIF/tamper heuristics. Local fallback: regex/heuristic for staged injection phrases so the demo never depends on network.
  - *Audit chain:* `hash = sha256(prev_hash + canonical(record_without_hash))`; append inside a Firestore transaction on an `audit_head` doc (or run gateway with max-instances=1 for the MVP).
  - *Containment:* rules → `out_of_scope`, `bad_signature` ×N, rate > threshold, screening hit ⇒ revoke credential, mark session quarantined, emit `SecurityEvent`, push to both consoles.
- **Done when:** impostor script and injected-document script are both blocked and visible in `/audit` and `/security-events`; tampering one stored record makes `/audit/verify` fail at that seq.

### M2 · Vendor Guardian  *(V, Day 3)*
- **Purpose:** keep the vendor's rules; decide, counter, sign.
- **Interfaces:**
  - `evaluate(proposal, mandate, committed_today) -> Decision`
  - `make_counter(proposal, mandate, committed_today) -> OrderProposal | None`
  - `sign_order(final_proposal, consent?) -> signature` — refuses unless decision green, or amber/red-accepted **with** a `ConsentRecord` whose `subject_hash == hash(final_proposal)`
  - `sign_standing_mandate(mandate, consent) -> signature`
  - A2A skills exposed: `order.propose`, `order.counter_response`, `terms.propose`, `quality.report`
- **Internals:** pure functions + a small state store (committed qty per day). Counter algorithm: clamp qty to `max_qty_per_day - committed`; move pickup to the earliest allowed window; raise price to floor; cap payment days; buyer not approved ⇒ `decline`. Within mandate and under `auto_accept.max_value` ⇒ green; within mandate but over auto-accept ⇒ amber; outside ⇒ red + counter.
- **Done when:** the 50 candles @ 08:00, 45-day case returns red (qty, pickup, payment) + counter 40 @ 10:00, 15 days; property tests (Hypothesis) prove it never signs outside the mandate without matching consent.

### M3 · Vendor Concierge + voice  *(V, Day 2 text, Day 3–4 voice)*
- **Purpose:** the vendor's only interface — talk, collect, read back, ask for consent.
- **Interfaces:**
  - `WS /voice/{vendor_id}` — browser streams 16 kHz PCM; server bridges to Gemini Live; returns audio + transcript + UI events
  - `POST /chat/{vendor_id}` — same agent in text (for dev, tests and fallback)
  - ADK tools (the whole action surface): `save_profile_field`, `submit_document(photo_uri, doc_type)`, `draft_listing(photo_uri, spoken_text)`, `confirm_listing(id)`, `draft_rules(spoken_text)`, `confirm_rules()`, `list_deals()`, `explain_decision(order_id)`, `capture_consent(subject_id)`, `what_did_you_do_today()`
  - **No signing tool exists on the Concierge.** `confirm_*` and `capture_consent` hand off to the Guardian.
- **Internals:** system prompt per Country Pack language from `config/prompts/`; Business Profile fields drive the onboarding checklist; every write tool returns a `readback` string the agent must speak before the confirm tool is allowed (tool guard checks a `pending_readback` flag). Consent: UI starts a short MediaRecorder clip when `capture_consent` fires → upload to BlobStore → transcript + `subject_hash` → `ConsentRecord`.
- **Done when:** Maya completes onboarding, two listings and rules in Telugu by voice; the same flow works by `/chat` in English for tests.

### M4 · Vendor API + A2A server  *(V, Day 2)*
- **Purpose:** state for the vendor app; inbound A2A for orders and terms.
- **Interfaces:** `GET /me`, `/listings`, `/rules` (rules card data with icons), `/deals` (with colour), `/deliveries`, `/activity` (from audit, filtered to this vendor); A2A server publishing the vendor agent's card.
- **Done when:** a proposal relayed by the gateway appears in `/deals` with the Guardian's colour within 1 s (SSE).

### M5 · Retailer Orchestrator  *(R, Day 2–3)*
- **Purpose:** plan each job, call specialists by capability, apply Retailer Policy, escalate.
- **Interfaces:**
  - `run(job_type, payload, retailer_id) -> JobResult`; job types: `onboard_vendor`, `build_listing`, `propose_terms`, `place_order`, `handle_counter`, `receive_delivery`
  - Approval queue: `GET /queue`, `POST /queue/{id}/approve|reject`
- **Internals:** each job is a declared workflow in config — list of capability steps (e.g., `build_listing = [catalogue.build, grade.*, compliance.check?]`). `grade.*` resolves to whatever the active Business Profile loads (Produce Grader for A, Label Checker for B) — this is the configurability proof. Policy gates between steps send items to the queue. Optional: a Gemini Pro "planner" only for free-text retailer requests (Could).
- **Done when:** switching ActiveConfig from A to B changes which specialists run for `build_listing` with zero code change; `handle_counter` auto-accepts within `auto_accept_counter_max_delta`, otherwise queues.

### M6 · Specialists  *(R, Days 2–5)*
Each lives in `retailer_svc/specialists/<name>/` with `card.json`, `handler.py` (`async def handle(input: In) -> Out`), `prompt.md`, and a tiny eval set in `tests/evals/<name>/`.

| Specialist | Capability | Day | Input → Output | LLM use | Must/Should |
|---|---|---|---|---|---|
| Onboarding & Verification | `vendor.verify` | 2 | doc photos → extracted fields, registry results, plain-language failure reasons | Flash vision extraction | Must |
| Catalogue Builder | `catalogue.build` | 2 | photo + transcript → `Listing` draft in retailer categories/units, claim flags | Flash vision + JSON | Must |
| Craft Quality Checker | `grade.craft` | 2 | photo → grade A–C, finish, defects, batch consistency, claims on the label, confidence | Flash vision | Must (A) |
| Produce Grader | `grade.produce` | 2 | photo → grade A–C, freshness, shelf-life days, confidence | Flash vision | Spare (in no preset) |
| Terms | `terms.propose` | 3 | vendor + template → terms proposal | none (template) | Must |
| Ordering / Replenishment Planner | `order.create` / `order.replenish` | 3 | demand feed + slots → `OrderProposal` per store | none (rules) | Must |
| Label & Food-Safety Checker | `grade.label` | 5 | label photo → ingredients, allergens, expiry, reg. number; check vs Country Pack regex | Flash vision | Must (B) |
| Supplier Compliance Auditor | `compliance.check` | 5 | vendor docs + labels → pass/flags vs chain standards | light | Must (B) |
| Quality & Disputes | `quality.report` | 5 | receiving photos + counts → report, dispute timeline | Flash vision (optional) | Should |

- **Done when:** each passes its contract test (schema in/out) and its eval set (≥ 8/10 on seeded photos); unsupported "organic" claim is flagged.

### M7 · Config & Registry store + Studio API  *(R/U, Day 1 + Day 5)*
- **Purpose:** versioned configs and Agent Cards; the live switch.
- **Interfaces:** `GET/PUT /config/{kind}/{id}` (new version on write), `POST /config/activate {retailer_id, refs}` → new `ActiveConfig` + hash, `GET /registry/cards`.
- **Done when:** activating config B emits an audit record with the new `config_hash` and both consoles reflect it without restart.

### M8 · Mock external services  *(U, Day 1–2)*
- **Purpose:** honest simulation of the outside world.
- **Interfaces:** `POST /id/verify`, `POST /bank/verify` (penny-drop style), `GET /fssai/{no}`, `GET /pirt/{no}`, `GET /demand?store=&date=`, `POST /receiving`.
- **Internals:** fixture-driven with deterministic failure cases (name mismatch, expired licence, unknown permit) so the "try again, here's why" path is demoable.
- **Done when:** each endpoint returns pass and fail cases from fixtures; labelled "simulated" in the UI.

### M9 · Vendor app (web `/vendor`)  *(U, Days 3–5)*
- **Screens:** Talk (one big push-to-talk, camera button, live captions), My rules (icon card + signature badge), Deals (green/amber/red cards, tap or say "yes"), Deliveries (Should), Activity ("who acted for me").
- **Internals:** AudioWorklet capture → WS; playback queue for 24 kHz audio; i18n strings from Country Pack; image downscale to ≤ 1024 px before upload; retry queue for poor connectivity.
- **Done when:** usable on a low-end Android in Chrome; whole demo path doable without typing.

### M10 · Retailer console (web `/retailer`)  *(U, Days 4–5)*
- **Screens:** Today (exceptions), Configuration studio (pick profiles/packs, toggle specialists, policy thresholds, Activate), Vendor pipeline, Catalogue review queue, Orders (live, with reasons), Trust & audit (agent identities, live activity, security events, consent records, **Verify log** button).
- **Done when:** the switch from A to B and the blocked attack are both visible live during the demo.

### M11 · Seed data, demo scripts, tests  *(U + all, continuous)*
- **Seed:** configs A/B; Maya (Telugu, hand-poured candles in Hyderabad, mandate: lavender candle ≥ ₹180, ≤ 40/day, pickup ≥ 10:00, paid ≤ 15 days); Sara (Bahasa Indonesia, snack with label missing P-IRT number); corporate gifting desk (`store:hub-4`) with demand 50 candles @ 08:00 on net-45 terms.
- **Scripts:** `demo_reset.py`, `e2e_order_loop.py` (text-only, runs in CI), `attack_impostor.py`, `attack_injected_doc.py` (+ `injected.pdf`).
- **Tests:** unit (Guardian, audit chain, policy, canonical JSON), contract (every Agent Card in/out schema), e2e (order loop green/amber/red), attack suite, specialist evals.

### M12 · Infra & deploy  *(T, Day 1 + Day 6)*
- `Dockerfile` per service, `cloudbuild.yaml`, `deploy.sh`; separate service accounts and KMS keyrings for vendor tenant vs retailer tenant (same project in MVP — say so openly; separate project in production); Secret Manager for API keys; Firebase Hosting for `web/`; `scripts/dev.sh` runs all four services + web locally with in-memory stores.

---

## 6. Order loop — call sequence (the spine)

```
retailer-svc  Orchestrator.run(place_order)
  └─ Registry.invoke(order.create) → OrderProposal v1 (50 @ 08:00, 45 days)
  └─ policy check (value ≤ auto_order_max) ─────────────── else → approval queue
  └─ gateway POST /tokens (scope=order.propose, aud=vendor-agent:maya)
  └─ gateway POST /relay/vendor-agent:maya/order.propose  [signed]
        trust-gateway: verify token+sig → scope/policy → screen → audit → forward
vendor-svc  Guardian.evaluate → red, counter v2 (40 @ 10:00, 15 days) → audit
  └─ returns Decision via gateway → retailer-svc
retailer-svc  Orchestrator.run(handle_counter) → within delta → accept v2 (else queue)
  └─ relay order.counter_response(accept v2)
vendor-svc  Concierge reads v2 to Maya (amber needs consent) → "yes" → ConsentRecord(hash v2)
  └─ Guardian.sign_order(v2, consent) → vendor_sig
retailer-svc signs → gateway POST /mandates/order → verify both sigs → audit → SSE to both UIs
```

---

## 7. Day-by-day build (critical path in bold)

| Day | V (vendor & voice) | R (retailer & specialists) | T (trust & audit) | U (UI, data, demo) |
|---|---|---|---|---|
| **12–13 Oct** | Vendor svc skeleton, A2A server, `/chat` text agent | Retailer svc skeleton, registry + local transport, Catalogue + Grader v0 | **vl_core schemas, crypto, store; gateway relay pass-through + audit chain** | Configs A/B, seed, mocks, web shell |
| 14 Oct | **Guardian + unit/property tests** | Onboarding & Verification, Terms, Replenishment | Tokens + request signing, credentials on approval | Vendor app Talk + Deals screens |
| 15 Oct | Gemini Live bridge, Telugu prompts | **Orchestrator workflows, handle_counter, policy queue** | Mandate store + signature verification | **`e2e_order_loop.py` green/amber/red passes** |
| 16 Oct | Voice consent capture, rules by voice | Label Checker, Compliance Auditor | Screening (Model Armor + fallback), containment, attack scripts | Console: orders, audit, security events |
| 17 Oct | Bahasa Indonesia pack, polish read-backs | Config B end to end, Quality (Should) | `/audit/verify`, deploy to Cloud Run | Config studio + live switch, review queue |
| 18 Oct | Demo rehearsal | Demo rehearsal | Final deploy, freeze | Record video, deck, submit |

**Cut order if behind:** Quality & Disputes → Pricing Advisor → review queue polish → voice for rules (keep text + voice consent only). Never cut: order loop, Guardian, gateway block, A→B switch.

---

## 8. Conventions

- Every cross-service payload is a `vl_core.schemas` model; no free-form dicts across boundaries.
- Every state change writes one `AuditRecord` (actor, on_behalf_of, action, payload_hash, config_hash).
- LLM outputs are always JSON-schema validated; failures fall back to "needs review", never to a silent default.
- Prompts live in `config/prompts/`, not in code.
- Feature flags via env; `.env.example` lists all of them.
- PRs: one module per PR, tests included; `make check` = ruff + mypy + pytest.

---

## 9. Decisions to confirm before scaffolding

1. D2/D3 — four services with in-process specialists (recommended) vs one Cloud Run service per specialist.
2. D4 — deterministic workflows for the orchestrator (recommended) vs an LLM planner.
3. Web stack — React + Vite + TypeScript, one app with two routes (recommended).
4. Team size and owners for tracks V / R / T / U.

---

## 10. Changes since v1 (13 Oct 2026)

CLAUDE.md is the source of truth; these ADRs record what changed from this plan and why:

- **ADR 0001** — MVP interfaces (schemas, gateway skills, containment, consent rules).
- **ADR 0002** — `agents/` package: registry, core agents, specialists, `config/agents.json`,
  standalone hosts, `prompts.py` per agent.
- **ADR 0003** — Gateway as agent control plane: agent registry, event stream, control API,
  tracing, trust and quality scores; supplier self-registration, directory and invites.
- **ADR 0004** — Human in the loop: Human Liaison, Slack/Gmail channel agents over MCP, routing
  and escalation config, durable LangGraph jobs (batch replenishment run), Concierge on Google ADK.

Effect on §5 modules: M3 Concierge now runs on Google ADK (text + Live); M5 Orchestrator is a
stateless agent and batch jobs run in LangGraph; M10 console adds Suppliers, Tasks, Observability
and a dev Outbox; new channels Slack and Gmail are in scope (CLAUDE.md "Scope guardrails").
