# ADR 0004 — Human in the loop: Liaison, Slack/Gmail over MCP, durable jobs, ADK

Status: proposed (built, needs team review) · 13 Oct 2026

## Problem
Agents either finished or failed. Anything they could not know (a missing price, a document
number, a buyer's approval) became a `needs_review` flag or a button that waited until someone
opened the app. Batch work (nightly replenishment) cannot assume anyone is watching a screen.

## Decisions
1. **Asking is a capability.** Specialists return `InfoRequest`s with their output
   (`ListingStepOut.requests`, `VendorCheckOut.requests`); jobs call LangGraph `interrupt()`.
   Nobody messages people directly.
2. **Human Liaison** (core agent, one per tenant host: `liaison@vendor-svc`, `liaison@retailer-svc`).
   `human.ask / answer / cancel / tick / list`. It renders the question in the recipient's
   language, picks channels from `config/hitl/routing.json` (kind × role → steps of channels,
   reminders, escalation to another role, expiry with `cancel | reject | keep`), and addresses
   from the party's contacts (`config/contacts/*.json` with `${ENV}` values; vendors from their
   own record). On an answer it calls the host's `hitl_resolved` hook, which resumes the work:
   `queue:<id>`, `job:<id>`, `listing:…`, `doc:…`, `info:<id>`, `deal:<order>`.
3. **Channel agents** (`slack`, `gmail`, `inapp`), registered like any agent. Slack and Gmail speak
   **MCP**: servers are configured only from the environment (`MCP_<SERVER>_*`, secrets passed
   by variable name), and our operations (send / reply / read) map to the server's tool names in
   `config/channels/*.json`, so any Slack or Gmail MCP server can be used without code changes
   (`python -m agents mcp-tools SLACK` lists a server's tools). With `VL_CHANNELS=local`
   messages go to a **dev outbox** (console "Outbox (dev)") where you can reply as the person.
4. **Who uses which channel.** Retailer staff and ops: Slack, then Gmail. Vendors: in-app and
   voice; Gmail only *nudges* them back to the app. **Consent stays in-app/voice** after the
   read-back, from her own words; a consent task cannot be answered by text (`answer_in_deals`).
5. **Reading replies is deterministic**: choices by word, numbers, dates, or free text; quoted
   email history is stripped; email replies count only from the address asked; free text is
   screened for prompt injection; an unclear reply gets one "please reply with …", never a guess.
6. **Durable jobs on LangGraph.** `replenishment_run` (Cloud Scheduler, nightly): plan every store
   → ask the vendor about demand she has no listing for (`info.request` through the gateway) →
   send orders within policy → `interrupt()` per order over the auto-order limit → send the
   approved ones → tell ops. State is checkpointed; an answer hours later resumes in place.
   **Checkpointer:** Postgres (Cloud SQL) in deploy, SQLite locally, memory in tests, behind
   `vl_core.checkpoints`. Postgres is LangGraph's maintained saver and safe across Cloud Run
   instances; SQLite is not (ephemeral, per-instance disk); a custom Firestore saver would be
   unmaintained code deciding whether a paused job can resume. Business state stays in Firestore.
7. **Concierge on Google ADK** (`vl_core/llm/adk.py`): `LlmAgent` + `Runner` for text, `run_live`
   for voice, our tools wrapped as ADK tools. Tested with a scripted ADK model: the model cannot
   consent for her (consent reads her own words).
8. **New gateway skills** (deny-by-default table): `info.request` (retailer → vendor) and
   `info.response` (vendor → retailer).

## Consequences
- Every ask and answer is an event (`ask` / `answer`) with role, channels and trace id; the
  Observability "People" view shows open asks, time to answer, expiries, by channel and role.
- The order loop and the Guardian are unchanged; amber deals additionally get reminders and
  expire as a decline (which notifies the retailer) instead of waiting forever.
- Not verified against real Slack/Gmail workspaces or real Gemini in this environment: the MCP
  path is tested end to end against stdio MCP test servers with the same tool names, and ADK
  against a scripted model.
