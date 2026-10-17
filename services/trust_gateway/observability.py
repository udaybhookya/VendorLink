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
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import quantiles
from typing import TYPE_CHECKING

from vl_core.schemas import (
    AgentCredential,
    AgentEvent,
    AgentMetrics,
    AgentObservation,
    HumanLoopSummary,
    QualityRow,
    SafetySummary,
    SecurityEvent,
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
