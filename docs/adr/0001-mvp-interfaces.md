# ADR 0001 — MVP interfaces and deviations from PLAN.md

Status: proposed (built, needs team review) · 12 Oct 2026

These decisions were made while building M0–M12 in one pass. Each is small and reversible, but
several change field names or flows that `docs/PLAN.md` describes, so whoever owns that module
should confirm or push back.

## 1. Packaging
One installable project (`pyproject.toml` at the root) that packages `packages/vl_core`,
`services/*` and `scripts` as top-level imports. CLAUDE.md says "uv workspace", but it also puts
code directly in `packages/vl_core/` and `services/vendor_svc/`. Separate workspace members would
need an extra nesting level, so this keeps the documented paths. **CLAUDE.md wording to update.**

## 2. Config additions (all data, rule 4)
| Where | What | Why |
|---|---|---|
| `config/presets/{A,B}.json` | named bundles of the four refs | seeding and the A→B switch |
| `config/workflows/default.json` | job type → capability steps (`grade.*`, `x?` optional) | M5 "workflows in config" |
| `config/i18n/{en,te,id}.json`, `console.en.json` | every user-facing string, reason-code wording, yes/no words, intents | no strings in code |
| `DocSpec.role` | business profiles require roles (`identity`, `food_safety_registration`); the country pack says which document fills each | keeps business profiles country-free |
| `CountryPack.timezone`, `label_keywords` | pickup windows are local time; label field words per country | Guardian and Label Checker |
| `RetailerProfile.workflow_set`, `claims_requiring_proof` | which workflows; which claims need proof | Orchestrator, Catalogue Builder |

## 3. Schema changes vs PLAN §4
- `StandingMandate.skus: [SkuRule{sku, unit, price_floor, max_qty_per_day}]` replaces the two
  maps; units are explicit per SKU. Adds `currency`, `timezone`.
- `StandingMandate.unsigned()` excludes `signature`, `signed_at` **and `consent_ref`**: consent is
  captured over the rules before its id exists, so the signed subject cannot include it.
- `OrderProposal` adds `vendor_id`, `currency`; it is frozen; `next_version()` makes counters.
- `Decision` adds `vendor_sig` (present when the Guardian signed a green order immediately).
- `AgentCredential` uses `party_type` + `on_behalf_of` (retailer agents need keys too) + `endpoint`.
- Extra reason codes: `PICKUP_AFTER_WINDOW`, `SKU_NOT_IN_MANDATE`, `CURRENCY_MISMATCH`,
  `NO_STANDING_MANDATE`, `DAILY_MAX_REACHED`. The last three, plus `BUYER_NOT_APPROVED`, decline.
- New cross-boundary models: `SignedOrder`, `CounterResponse`, `Deal`, `RetailerOrder`,
  `ListingSubmission`, `VendorApplication`, `VendorStatusUpdate`, `RulesDraft`, `ConsentRequest`,
  specialist I/O (`ListingStepIn/Out`, `VendorCheckIn/Out`, `OrderPlanIn/Out`, `TermsIn/Out`),
  gateway API bodies, mock registry I/O.

## 4. Trust gateway
- **Every write is a signed request** (`X-VL-Agent`, `X-VL-JTI`, `X-VL-Signature` over
  `canonical({method, path, body_hash, jti})`), including `/tokens`, `/audit`, `/screen`,
  `/consents`, `/mandates/*`. Request ids are single-use (replay → `bad_token` event).
- **Tokens are single-use**: the relay's request id must equal the token's `jti`. One token, one relay.
- **Forwarding**: the gateway re-signs as `platform:gateway`; A2A servers accept only that
  signature (`vl_core.a2a.server`). A caller that holds a token still cannot bypass the gateway.
- **Skill table** (deny by default, typed body and response per skill):
  retailer→vendor `order.propose`, `order.counter_response`, `terms.propose`, `vendor.status`;
  vendor→retailer `order.vendor_signed`, `vendor.onboard`, `listing.submit`, `listing.confirm`.
- **Containment differs from PLAN M1**: `bad_signature` / `bad_token` are blocked and recorded but
  **never revoke**. The identity is unproven, so revoking would let an impostor lock out the
  real agent. Revocation is automatic only for verified agents (`out_of_scope`, injection in a
  relayed message, rate). An injected *document* quarantines the vendor named as `subject`.
- **Policy**: the gateway checks parties named in the body against the token, and the buyer
  allow-list from the stored standing mandate. The Retailer Policy value cap is enforced by the
  Orchestrator (approval queue), not the gateway: a human-approved order may exceed the cap.
- Read endpoints (`GET /audit`, `/credentials`, …) are unauthenticated for the demo consoles.

## 5. Flows
- **Agent credentials are issued when a vendor is registered** (by the vendor-tenant facilitator
  using the bootstrap admin token), not on approval. Approval is enforced on the retailer side:
  orders only go to `approved` vendors. PLAN M1 says "on vendor approval".
- **Listings are built on the retailer side**: the vendor sends photo + words (`listing.submit`),
  the retailer runs `build_listing`, the vendor reads back and confirms (`listing.confirm`).
- **Compliance Auditor** runs in `onboard_vendor` only (documents + listings); one handler cannot
  serve two input contracts.
- **Consent** must echo the `subject_hash` the vendor was read back, and must contain a yes word
  from her language's i18n list (deterministic). In chat and voice the transcript is the vendor's
  own last words, never text the model supplies.

## 6. Concierge and voice
- No ADK. The conversational model sits behind `vl_core.llm.ToolChat` (Gemini function calling)
  and voice behind `vl_core.llm.live.LiveBridge` (Gemini Live), like every other GCP dependency.
- Without `PRO_MODEL` the Concierge runs an **offline keyword mode** (deals, rules, today, yes/no)
  so tests and demos work without a model. Without `LIVE_MODEL` the web app falls back to the
  browser's own speech recognition and speech synthesis.
- `GeminiToolChat` and `GeminiLiveBridge` are **untested against the real API** (no credentials
  in this environment).

## 7. Not done yet
- Cloud adapters: `FirestoreRepo`, `KmsSigner`, `GcsBlobStore`, Model Armor screener raise
  `NotImplementedError`. Local fallbacks are complete.
- Quality & Disputes specialist, Pricing Advisor (first items on the cut list).
- Produce Grader eval needs 10 photos in `seed/photos/` and `FLASH_MODEL`.
- Vendor app has no login: the vendor id is in the URL path (demo only).
- Native-speaker review of Telugu and Bahasa strings in `config/i18n/` and the country packs.
