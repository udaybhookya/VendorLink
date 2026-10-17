"""Agent scores, trust scores, traces, safety and quality, computed from the event feed.

Deterministic and explainable: every trust score comes with the factors that moved it.

Trust score (0–100), starting from 100; weights from `config/scoring/trust.json` (defaults):
  status            suspended −40 · quarantined −60 · revoked → 0
  violations        −25 per verified violation by the agent (out_of_scope, injection, rate), max −75
  targeted          −2 per impersonation attempt in its name (bad_signature/bad_token), max −10
  errors            −30 × error rate of its calls
  denials           −20 × share of its calls that were denied
  quality           −20 × needs-review rate (specialists)
Quality score (specialists): 100 × (1 − needs-review rate) × success rate.

Also: an overview (agents per side, messages, tokens, authentication and authorisation), the
vendor-agent ↔ retailer-agent connections, timings of steps and whole flows, and one agent's
detail (auth, connections, timings, its own events).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import quantiles
from typing import TYPE_CHECKING

from vl_core.schemas import (
    ActivitySummary,
    ActivityTiming,
    AgentCredential,
    AgentDetail,
    AgentEvent,
    AgentMetrics,
    AgentObservation,
    AuthSummary,
    Connection,
    HumanLoopSummary,
    ObservabilityOverview,
    QualityRow,
    SafetySummary,
    SecurityEvent,
    SideSummary,
    TraceSummary,
    TraceView,
    TrustFactor,
    TrustScoring,
)

if TYPE_CHECKING:
    from trust_gateway.gateway import Gateway

LIVE_WINDOW = timedelta(minutes=2)
VIOLATIONS = {"out_of_scope", "injection", "rate"}
IMPERSONATION = {"bad_signature", "bad_token"}


def _percentile(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(quantiles(values, n=100, method="inclusive")[q - 1], 1)


def metrics_for(agent_id: str, events: list[AgentEvent], now: datetime) -> AgentMetrics:
    calls = [e for e in events if e.type in ("call", "decision") and e.agent_id == agent_id]
    calls = [e for e in calls if e.type == "call"] or calls
    errors = sum(1 for e in calls if e.outcome == "error")
    denied = sum(
        1
        for e in events
        if e.agent_id == agent_id
        and e.outcome == "denied"
        and not (e.type == "security" and e.action in IMPERSONATION)  # unproven identity
    )
    latencies = [e.latency_ms for e in calls if e.latency_ms is not None]
    reviews = [e.attrs["needs_review"] for e in calls if "needs_review" in e.attrs]
    decisions = Counter(
        str(e.attrs.get("status"))
        for e in events
        if e.type == "decision" and e.agent_id == agent_id and "status" in e.attrs
    )
    own_work = [e for e in events if e.agent_id == agent_id and e.type in ("call", "decision")]
    n = len(calls)
    return AgentMetrics(
        calls=n,
        errors=errors,
        denied=denied,
        success_rate=round(1 - errors / n, 3) if n else 1.0,
        p50_ms=_percentile(latencies, 50),
        p95_ms=_percentile(latencies, 95),
        calls_last_5m=sum(1 for e in calls if now - e.ts <= timedelta(minutes=5)),
        needs_review_rate=round(sum(bool(r) for r in reviews) / len(reviews), 3)
        if reviews
        else None,
        decisions=dict(decisions),
        tokens_in=sum(_num(e, "tokens_in") for e in own_work),
        tokens_out=sum(_num(e, "tokens_out") for e in own_work),
        model_calls=sum(_num(e, "model_calls") for e in own_work),
    )


def trust_for(
    cred: AgentCredential,
    effective: str,
    m: AgentMetrics,
    security: list[SecurityEvent],
    w: TrustScoring | None = None,
) -> tuple[int, list[TrustFactor]]:
    w = w or TrustScoring()
    factors: list[TrustFactor] = []
    if effective == "revoked":
        return 0, [TrustFactor(name="revoked", impact=-100, detail=cred.status_reason)]
    if effective in ("suspended", "quarantined"):
        impact = -(w.suspended if effective == "suspended" else w.quarantined)
        factors.append(TrustFactor(name=effective, impact=impact, detail=cred.status_reason))
    own = [e for e in security if e.agent_id == cred.agent_id]
    violations = [e for e in own if e.type in VIOLATIONS]
    if violations:
        factors.append(
            TrustFactor(
                name="violations",
                impact=-min(w.max_violations, w.per_violation * len(violations)),
                detail=", ".join(sorted({e.type for e in violations})),
            )
        )
    targeted = [e for e in own if e.type in IMPERSONATION]
    if targeted:
        factors.append(
            TrustFactor(
                name="impersonation attempts",
                impact=-min(w.max_impersonation, w.per_impersonation * len(targeted)),
                detail=f"{len(targeted)} blocked calls claimed this identity",
            )
        )
    if m.calls and m.errors:
        factors.append(
            TrustFactor(
                name="errors",
                impact=round(-w.error_rate * m.errors / m.calls, 1),
                detail=f"{m.errors}/{m.calls} calls failed",
            )
        )
    if m.denied:
        rate = m.denied / max(m.calls + m.denied, 1)
        factors.append(
            TrustFactor(
                name="denied", impact=round(-w.denial_rate * rate, 1), detail=f"{m.denied} denied"
            )
        )
    if m.needs_review_rate:
        factors.append(
            TrustFactor(
                name="needs review",
                impact=round(-w.needs_review_rate * m.needs_review_rate, 1),
                detail=f"{m.needs_review_rate:.0%} of outputs need a human",
            )
        )
    score = max(0, min(100, round(100 + sum(f.impact for f in factors))))
    return score, factors


async def observe_agents(gw: Gateway) -> list[AgentObservation]:
    now = datetime.now(UTC)
    events = list(gw.control.events)
    security = await gw.store.events.query()
    frozen = await gw.control.frozen()
    out = []
    for cred in await gw.store.credentials.query():
        effective = await gw.control.effective(cred, frozen)
        m = metrics_for(cred.agent_id, events, now)
        score, factors = trust_for(cred, effective, m, security, gw.trust_scoring)
        quality = None
        if cred.kind == "specialist" and m.calls:
            quality = round(100 * (1 - (m.needs_review_rate or 0)) * m.success_rate)
        live = (
            effective == "active"
            and cred.last_seen is not None
            and now - cred.last_seen <= LIVE_WINDOW
        )
        out.append(
            AgentObservation(
                agent=cred,
                effective_status=effective,
                live=live,
                metrics=m,
                trust_score=score,
                trust_factors=factors,
                quality_score=quality,
            )
        )
    return sorted(out, key=lambda o: (o.agent.kind, o.agent.agent_id))


def traces(gw: Gateway, limit: int = 50) -> list[TraceSummary]:
    grouped: dict[str, list[AgentEvent]] = defaultdict(list)
    for e in gw.control.events:
        if e.trace_id:
            grouped[e.trace_id].append(e)
    summaries = [summarise(tid, evs) for tid, evs in grouped.items()]
    return sorted(summaries, key=lambda t: t.started, reverse=True)[:limit]


def summarise(trace_id: str, events: list[AgentEvent]) -> TraceSummary:
    events = sorted(events, key=lambda e: e.ts)
    start = events[0].ts
    end = max(e.ts + timedelta(milliseconds=e.latency_ms or 0) for e in events)
    outcome = (
        "error"
        if any(e.outcome == "error" for e in events)
        else "denied"
        if any(e.outcome == "denied" for e in events)
        else "ok"
    )
    return TraceSummary(
        trace_id=trace_id,
        started=start,
        duration_ms=round((end - start).total_seconds() * 1000, 1),
        agents=sorted(
            {e.agent_id for e in events}
            | {e.target for e in events if e.target.count(":") == 1 and "-" in e.target}
        ),
        actions=list(dict.fromkeys(e.action for e in events))[:12],
        outcome=outcome,
        events=len(events),
    )


def trace(gw: Gateway, trace_id: str) -> TraceView | None:
    events = [e for e in gw.control.events if e.trace_id == trace_id]
    if not events:
        return None
    return TraceView(summary=summarise(trace_id, events), events=sorted(events, key=lambda e: e.ts))


async def safety(gw: Gateway) -> SafetySummary:
    creds = await gw.store.credentials.query()
    return SafetySummary(
        security_events=dict(Counter(e.type for e in await gw.store.events.query())),
        screening_blocked=sum(
            1 for e in gw.control.events if e.type == "screening" and e.outcome == "denied"
        ),
        quarantined_subjects=sorted(q.subject for q in await gw.store.quarantine.query()),
        frozen=await gw.control.frozen(),
        agents_by_status=dict(Counter(c.status for c in creds)),
    )


async def quality(gw: Gateway) -> list[QualityRow]:
    now = datetime.now(UTC)
    events = list(gw.control.events)
    rows = []
    for cred in await gw.store.credentials.query(kind="specialist"):
        m = metrics_for(cred.agent_id, events, now)
        flags: Counter[str] = Counter()
        for e in events:
            if e.agent_id == cred.agent_id and e.type == "call" and e.attrs.get("flags"):
                flags.update(str(e.attrs["flags"]).split(","))
        q = round(100 * (1 - (m.needs_review_rate or 0)) * m.success_rate) if m.calls else None
        rows.append(
            QualityRow(
                agent_id=cred.agent_id,
                calls=m.calls,
                needs_review_rate=m.needs_review_rate,
                error_rate=round(m.errors / m.calls, 3) if m.calls else 0.0,
                top_flags=dict(flags.most_common(5)),
                quality_score=q,
            )
        )
    return rows


def humans(gw: Gateway) -> HumanLoopSummary:
    asks: dict[str, AgentEvent] = {}
    answers: dict[str, AgentEvent] = {}
    for e in gw.control.events:
        task = str(e.attrs.get("task", ""))
        if not task or e.attrs.get("task_kind") == "notice":  # notices tell; they do not ask
            continue
        if e.type == "ask":
            asks.setdefault(task, e)
        elif e.type == "answer":
            answers[task] = e
    minutes = sorted(
        (answers[t].ts - asks[t].ts).total_seconds() / 60
        for t in answers
        if t in asks and answers[t].attrs.get("status") == "answered"
    )
    answered = [a for a in answers.values() if a.attrs.get("status") == "answered"]
    return HumanLoopSummary(
        open=sum(1 for t in asks if t not in answers),
        answered=len(answered),
        expired=sum(1 for a in answers.values() if a.attrs.get("status") == "expired"),
        median_minutes_to_answer=round(minutes[len(minutes) // 2], 1) if minutes else None,
        by_channel=dict(Counter(str(a.attrs.get("via", "?")) for a in answered)),
        by_role=dict(Counter(str(e.attrs.get("role", "?")) for e in asks.values())),
        recent=sorted([*asks.values(), *answers.values()], key=lambda e: e.ts, reverse=True)[:30],
    )


# ---- overview, connections, activity timings, per-agent detail --------------------------------

AUTHN = {"bad_signature", "replay", "bad_token", "session_ended"}
TALKING_WINDOW = timedelta(minutes=15)


def _num(e: AgentEvent, key: str) -> int:
    value = e.attrs.get(key)
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _is_denial(e: AgentEvent) -> bool:
    return e.outcome == "denied" and e.type in ("permission_check", "security")


def _is_authn(e: AgentEvent) -> bool:
    return _is_denial(e) and e.action in AUTHN


def _relay_target(path: str) -> str | None:
    """The receiving agent of a refused relay call, from the request path."""
    parts = path.split("/")
    return parts[2] if len(parts) >= 4 and parts[1] == "relay" else None


def _pair(
    a: AgentCredential | None, b: AgentCredential | None
) -> tuple[AgentCredential, AgentCredential] | None:
    """(vendor agent, retailer agent) when a and b sit on opposite sides, else None."""
    if a is None or b is None:
        return None
    sides = {a.party_type: a, b.party_type: b}
    if set(sides) != {"vendor", "retailer"}:
        return None
    return sides["vendor"], sides["retailer"]


def _is_relay(e: AgentEvent, creds: dict[str, AgentCredential]) -> bool:
    """A cross-side message the gateway relayed (only relay call events carry a target agent)."""
    return (
        e.type == "call"
        and _pair(creds.get(e.agent_id), creds.get(e.target)) is not None
        and e.agent_id in creds
    )


def _group(cred: AgentCredential) -> str | None:
    if cred.kind == "host":
        return None
    if cred.kind == "party":
        return cred.party_type if cred.party_type in ("vendor", "retailer") else None
    return "hosted"


async def overview(gw: Gateway) -> ObservabilityOverview:
    now = datetime.now(UTC)
    events = list(gw.control.events)
    creds = {c.agent_id: c for c in await gw.store.credentials.query()}
    frozen = await gw.control.frozen()
    recent_activity: set[str] = set()
    for e in events:
        if now - e.ts > TALKING_WINDOW:
            continue
        if _is_relay(e, creds):
            recent_activity.update((e.agent_id, e.target))
        elif e.type in ("call", "decision") and not e.target:
            recent_activity.add(e.agent_id)
    sides = {k: SideSummary() for k in ("vendor", "retailer", "hosted")}
    for cred in creds.values():
        group = _group(cred)
        if group is None:
            continue
        side = sides[group]
        effective = await gw.control.effective(cred, frozen)
        side.total += 1
        if effective == "active":
            side.active += 1
            if cred.last_seen is not None and now - cred.last_seen <= LIVE_WINDOW:
                side.live += 1
        else:
            side.blocked += 1
        if cred.agent_id in recent_activity:
            side.talking += 1
    relays = [e for e in events if _is_relay(e, creds)]
    return ObservabilityOverview(
        sides=sides,
        connections=len(_connections(events, creds)),
        messages=len(relays),
        tokens_in=sum(_num(e, "tokens_in") for e in events),
        tokens_out=sum(_num(e, "tokens_out") for e in events),
        model_calls=sum(_num(e, "model_calls") for e in events),
        logins=sum(1 for e in events if e.type == "login"),
        authn_failures=sum(1 for e in events if _is_authn(e)),
        authz_denials=sum(1 for e in events if _is_denial(e) and not _is_authn(e)),
        p95_relay_ms=_percentile([e.latency_ms for e in relays if e.latency_ms is not None], 95),
    )


def _connections(
    events: list[AgentEvent], creds: dict[str, AgentCredential]
) -> dict[tuple[str, str], Connection]:
    pairs: dict[tuple[str, str], Connection] = {}
    latencies: dict[tuple[str, str], list[float]] = defaultdict(list)

    def connection(v: AgentCredential, r: AgentCredential, ts: datetime) -> Connection:
        key = (v.agent_id, r.agent_id)
        conn = pairs.get(key)
        if conn is None:
            conn = pairs[key] = Connection(
                vendor_agent=v.agent_id,
                vendor=v.on_behalf_of,
                retailer_agent=r.agent_id,
                retailer=r.on_behalf_of,
                first_seen=ts,
            )
        conn.first_seen = min(conn.first_seen or ts, ts)
        conn.last_seen = max(conn.last_seen or ts, ts)
        return conn

    for e in events:
        if e.type == "call":
            pair = _pair(creds.get(e.agent_id), creds.get(e.target))
            if pair is None:
                continue
            conn = connection(*pair, e.ts)
            conn.messages += 1
            if creds[e.agent_id].party_type == "vendor":
                conn.to_retailer += 1
            else:
                conn.to_vendor += 1
            conn.skills[e.action] = conn.skills.get(e.action, 0) + 1
            if e.outcome == "error":
                conn.errors += 1
            if e.latency_ms is not None:
                latencies[(pair[0].agent_id, pair[1].agent_id)].append(e.latency_ms)
        elif e.type == "decision" and "status" in e.attrs:
            pair = _pair(creds.get(e.agent_id), creds.get(e.target))
            if pair is not None:
                conn = connection(*pair, e.ts)
                status = str(e.attrs["status"])
                conn.decisions[status] = conn.decisions.get(status, 0) + 1
        elif _is_denial(e):
            receiver = _relay_target(e.target)
            pair = _pair(creds.get(e.agent_id), creds.get(receiver or ""))
            if pair is not None:
                connection(*pair, e.ts).denied += 1
    for key, values in latencies.items():
        pairs[key].p95_ms = _percentile(values, 95)
    return pairs


async def connections(gw: Gateway, agent_id: str | None = None) -> list[Connection]:
    creds = {c.agent_id: c for c in await gw.store.credentials.query()}
    rows = list(_connections(list(gw.control.events), creds).values())
    if agent_id:
        rows = [c for c in rows if agent_id in (c.vendor_agent, c.retailer_agent)]
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(rows, key=lambda c: c.last_seen or epoch, reverse=True)


def _timing(name: str, rows: list[tuple[AgentEvent, float | None]]) -> ActivityTiming:
    durations = [d for _, d in rows if d is not None]
    return ActivityTiming(
        activity=name,
        count=len(rows),
        p50_ms=_percentile(durations, 50),
        p95_ms=_percentile(durations, 95),
        max_ms=round(max(durations), 1) if durations else None,
        errors=sum(1 for e, _ in rows if e.outcome == "error"),
        denied=sum(1 for e, _ in rows if e.outcome == "denied"),
        tokens_in=sum(_num(e, "tokens_in") for e, _ in rows),
        tokens_out=sum(_num(e, "tokens_out") for e, _ in rows),
        agents=sorted({e.agent_id for e, _ in rows})[:12],
    )


def _step_name(e: AgentEvent, creds: dict[str, AgentCredential]) -> str:
    return f"relay:{e.action}" if e.target in creds and e.type == "call" else e.action


def _steps(
    events: list[AgentEvent], creds: dict[str, AgentCredential], agent_id: str | None = None
) -> list[ActivityTiming]:
    """Timed work: in-process agent calls (capability) and relayed messages (relay:skill).
    Gateway decision events repeat a relay's outcome without a duration, so they are skipped."""
    grouped: dict[str, list[tuple[AgentEvent, float | None]]] = defaultdict(list)
    for e in events:
        if agent_id and e.agent_id != agent_id:
            continue
        if e.type in ("call", "decision") and e.latency_ms is not None:
            grouped[_step_name(e, creds)].append((e, e.latency_ms))
        elif e.type == "call" and e.outcome == "error":
            grouped[_step_name(e, creds)].append((e, None))
    for e in events:  # calls the registry refused (blocked agent, missing permission)
        mine = not agent_id or e.agent_id == agent_id
        if mine and e.type == "permission_check" and e.action in grouped:
            grouped[e.action].append((e, None))
    rows = [_timing(name, rows) for name, rows in grouped.items()]
    return sorted(rows, key=lambda t: (-(t.p95_ms or 0), t.activity))


async def activities(gw: Gateway) -> ActivitySummary:
    events = list(gw.control.events)
    creds = {c.agent_id: c for c in await gw.store.credentials.query()}
    traced: dict[str, list[AgentEvent]] = defaultdict(list)
    for e in events:
        if e.trace_id:
            traced[e.trace_id].append(e)
    flows: dict[str, list[tuple[AgentEvent, float | None]]] = defaultdict(list)
    for trace_id, evs in traced.items():
        evs.sort(key=lambda e: e.ts)
        root = evs[0]
        summary = summarise(trace_id, evs)
        # One row per flow: carry the flow's outcome and token totals on a synthetic event.
        flow_event = root.model_copy(
            update={
                "outcome": summary.outcome,
                "attrs": {
                    "tokens_in": sum(_num(e, "tokens_in") for e in evs),
                    "tokens_out": sum(_num(e, "tokens_out") for e in evs),
                },
            }
        )
        flows[_step_name(root, creds)].append((flow_event, summary.duration_ms))
    return ActivitySummary(
        steps=_steps(events, creds),
        flows=sorted(
            (_timing(name, rows) for name, rows in flows.items()),
            key=lambda t: (-(t.p95_ms or 0), t.activity),
        ),
    )


def _auth(cred: AgentCredential, events: list[AgentEvent]) -> AuthSummary:
    own = [e for e in events if e.agent_id == cred.agent_id]
    logins = [e for e in own if e.type == "login"]
    return AuthSummary(
        key_fingerprint=cred.key_fingerprint,
        host=cred.host,
        permissions=cred.permissions,
        rate_limit_per_min=cred.rate_limit_per_min,
        sessions_valid_after=cred.sessions_valid_after,
        key_rotation_requested=cred.key_rotation_requested,
        logins=len(logins),
        last_login=max((e.ts for e in logins), default=None),
        scopes=dict(Counter(e.action for e in logins)),
        authn_failures=dict(Counter(e.action for e in own if _is_authn(e))),
        authz_denials=dict(Counter(e.action for e in own if _is_denial(e) and not _is_authn(e))),
    )


async def agent_detail(gw: Gateway, agent_id: str) -> AgentDetail | None:
    observation = next((o for o in await observe_agents(gw) if o.agent.agent_id == agent_id), None)
    if observation is None:
        return None
    events = list(gw.control.events)
    creds = {c.agent_id: c for c in await gw.store.credentials.query()}
    return AgentDetail(
        observation=observation,
        auth=_auth(observation.agent, events),
        connections=await connections(gw, agent_id),
        activities=_steps(events, creds, agent_id),
        events=gw.control.recent(agent_id=agent_id, limit=200),
    )
