# ADR 0013 — Routine reads never block agents; a failed document is explained and asked again

Status: accepted · 18 Oct 2026 · `agents/registry.py`, trust gateway rate limit, onboarding
verifier, vendor-svc onboarding, console refresh

## Problem
Running the demo scenes live showed two classes of failure:

1. **A console could lock out the Liaison and get a service revoked.** The console re-reads the
   open tasks (`human.list`) on every server event, twice per refresh (Inbox badge and page). A
   reset plus the `waiting` scene sends dozens of events, so one open console made over 120
   Liaison calls a minute. The in-process registry then blocked the Liaison (`agent_blocked:
   rate limit`), so the buyer's approval, `/dev/tick` and placing orders all failed; refused
   calls still counted, so the block lasted as long as the reads did. Every call was also posted
   to the gateway as an event under the host credential; past 120 a minute the gateway's rate
   check **revoked `host:retailer-svc`**, and its telemetry stayed refused until the next reset.
2. **A vendor whose document failed was left without guidance.** Only missing documents became
   questions; a `NAME_MISMATCH`, `NOT_FOUND`, `BAD_FORMAT` or `EXPIRED` number only showed as a
   failed pill. Applying again skipped failed numbers, so the check said `MISSING_DOCUMENT`
   instead of the real reason. Document questions had a fixed task id, and `human.ask` returns
   an existing task even when closed, so a second wrong number was never asked about again.

## Decisions
1. **Routine reads are free.** `ROUTINE_CAPABILITIES` (`human.tick`, `human.list`,
   `channel.*.poll`, `guardian.mandate`) are still status- and permission-checked, but do not
   count against the agent's per-minute limit, and are reported as events only when they used a
   model. A refused call never counts, in the registry or in the gateway's `RateLimiter`, so a
   caller over the limit recovers one minute after its last accepted call.
2. **A host over the rate limit is refused, not revoked.** Hosts carry telemetry and
   registration only; revocation stays for party agents (the existing
   `test_rate_limit_revokes`) and for every other incident type.
3. **The console coalesces refreshes** (at most one per 300 ms, `coalesce` in `web/src/lib/api.ts`,
   also in the vendor app) and reads the queue and tasks once for the badge and the Inbox.
4. **Fixable document failures become questions.** The onboarding verifier adds an
   `InfoRequest` (`doc:<type>`, `hitl.q.doc_failed.<CODE>` in en/te/id) for each fixable failure,
   as it already did for missing documents; her answer re-saves the number and re-applies.
5. **Failed numbers are checked again on Apply**, so she sees the real reason; only a document
   blocked in screening (`BLOCKED_BY_SCREENING`) is never sent on.
6. **One document question per number checked**: the task id includes a hash of the number, so
   applying again with the same number does not ask twice, and a new failing number is asked
   about again.

## Not changed
No schema, gateway skill or policy change. The supplier still reaches the retailer's Inbox only
once every check passes. Consent rules are untouched.

## Checked
`tests/e2e/test_demo_scenes.py` (Sam's failed documents asked and re-asked, in Telugu too; the
`waiting` scene end to end; config B with Sara; suspending the terms agent),
`test_screens_rereading_tasks_never_block_the_liaison` (200 reads, then approve, tick and no
revoked host), gateway unit tests for host-not-revoked and refused-not-counted, and the
onboarding verifier eval set extended with the questions she is asked (10/10).
