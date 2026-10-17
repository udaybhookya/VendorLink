# ADR 0003 — Gateway as agent control plane; observability; supplier network

Status: proposed (built, needs team review) · 13 Oct 2026

## Decisions

**Agent registry at the gateway.** Every agent has a record (`AgentCredential`): id, kind
(`party` / `host` / `core` / `specialist`), tenant, host, key fingerprint, permissions, rate limit,
last seen, and status `active | suspended | quarantined | revoked`. Agents that run in-process
(Guardian, Concierge, Orchestrator, specialists) are registered by their host
(`host:vendor-svc`, `host:retailer-svc`) and act under the host's key, so the gateway can see and
stop them although their calls never cross it.

**One event format** (`AgentEvent`) for calls, decisions, logins, permission checks, screening,
control actions and audit appends. `GET /events/stream` is the live operational feed;
`GET /events/recent` the buffer (in memory, last 20,000 events). The hash-chained audit log stays
the formal record. Hosts report in-process calls via `POST /events` (signed, own agents only).

**Control API** (`POST /control/agents/{id}/{action}`, `POST /control/tenants/{t}/freeze|unfreeze`)
with an operator token and a named `actor` on every request: suspend, resume, quarantine, revoke
(final), rotate key, set permissions, set rate limit, end sessions, freeze a tenant
(`vendor`, `retailer`, or one party). Each action is written to the audit log as
`operator:<name>` and to the event feed.

**Status checked on every path.** The gateway checks the caller and the target on every token
and relay (status, tenant freeze, subject quarantine, per-agent permissions and rate limit, and
session cut-off via the token's `iat_ms`). In-process, `AgentRegistry` checks a cached snapshot
(`GET /agents/status`, TTL 2 s) before each call. **If the gateway is unreachable, the host uses
its last snapshot, or allows calls if it has none** (availability over control in the MVP).

**Key rotation** is requested by an operator and performed by the owning host on its next status
refresh: new key under a staging id → `POST /credentials/rotate` signed with the old key →
promote. Sessions issued before the rotation end.

**Tracing.** OpenTelemetry spans tagged `vl.agent_id` and `vl.on_behalf_of`; W3C `traceparent`
injected on every outgoing call and continued by middleware in every service. `VL_TRACE=cloud`
exports to Cloud Trace; ids exist even with export off, so the dashboard groups events by trace.

**Scores** (gateway, deterministic, explained). Trust 0–100 from status, verified violations,
impersonation attempts in the agent's name, error rate, denial rate and needs-review rate, each
returned as a factor. Attempts made in an agent's name *with an unproven identity* never count as
its own denials. Quality (specialists) = (1 − needs-review rate) × success rate.

**Supplier network.** `POST /vendors` (self-registration creates the vendor's agent),
`GET /options`, `/directory/retailers`, `/vendors/{id}/required-docs`, `/vendors/{id}/invites`;
retailer `GET /retailers/{id}/directory`, `POST …/suppliers/{vendor}/invite`. New gateway skills:
`vendor.invite` (retailer → vendor) and `order.vendor_declined` (vendor → retailer), so a vendor's
"no" closes the retailer's order.

## Not yet
- Control and observability reads are for one platform operator (operator token); per-retailer
  scoping of who may suspend what is not built.
- The event buffer and freezes live in gateway memory with the rest of local state (Firestore in
  deploy, with everything else).
- Evals are not yet fed into quality scores.
