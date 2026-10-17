# ADR 0006 — Logging you can follow

Status: accepted · 17 Oct 2026

## Context
The whole codebase had seven log calls. Running `make dev` showed uvicorn's access lines and
little else, so a failed chat (a Gemini 503 surfaced as a bare 500) or a stuck consent could not
be understood from the terminal. Four services write to one terminal locally, and to Cloud
Logging when deployed.

## Decisions
1. **One API:** `log = get_logger("area")`, then `log.info("what happened", key=value)`.
   Keyword arguments become fields (`fields=` takes a ready-made dict). Every line also carries
   the service, the party the request acts for and the trace id, from context variables.
2. **Two formats, one switch:** `VL_LOG_FORMAT=auto` prints one readable line per event on a
   terminal and JSON (with Cloud Logging's `severity`) when there is none, as on Cloud Run.
   `pretty` and `json` force either.
3. **Levels say who should care:**
   - INFO: what happened. State changes, Guardian decisions with reason codes, agent calls,
     model calls, relays, questions to people, job steps, config activation, and requests a
     person or script made.
   - DEBUG: routine reads (the screens poll), calls between services, and platform plumbing
     (registration, telemetry, token issue).
   - WARNING: refusals and blocks (`refused: <code>`, `security event`, rejected model output
     with its reason).
   - ERROR: failures, with the traceback.
4. **Choke points, not scattered prints:** the request middleware (one line per request), the
   agent registry (every agent call, block and failure), each service's `_audit` (every state
   change, CLAUDE.md rule 6), the gateway's `record` (its own events) and relay (one summary
   per cross-tenant message), the LLM client and ADK runtime, the Liaison, channels and MCP
   client, and the job runner. Uvicorn's access log is off (`--no-access-log`) because the
   middleware line carries more.
5. **Privacy:** ids, codes, counts, durations and hashes only. Chat text, documents, audio,
   names and contact addresses are never logged. The Concierge logs tool names and `*_id`
   arguments, not values the vendor typed or said.

## Consequences
- One order can be followed across services by its trace id, in the terminal or the console's
  Observability page.
- `LOG_LEVEL=DEBUG` restores the full picture when needed, without code changes.
- New code follows the CLAUDE.md logging rule: an INFO line for each state change or decision.
