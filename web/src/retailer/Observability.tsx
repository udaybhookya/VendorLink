import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { AgentEvent, AgentObservation, HumanLoopSummary, QualityRow, SafetySummary, TraceSummary, TraceView } from "../types/generated";
import { api, GATEWAY_URL, shortHash } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Icon } from "../lib/icons";

type Sub = "agents" | "traces" | "logs" | "safety" | "quality" | "humans";

function useOperator(): [{ name: string; token: string }, (v: { name: string; token: string }) => void] {
  const read = () => {
    try {
      return JSON.parse(sessionStorage.getItem("vl.operator") ?? "") as { name: string; token: string };
    } catch {
      return { name: "", token: "" };
    }
  };
  const [op, setOp] = useState(read);
  return [op, (v) => {
    setOp(v);
    try {
      sessionStorage.setItem("vl.operator", JSON.stringify(v));
    } catch {
      /* session storage blocked: keep it in memory */
    }
  }];
}

export function Observability(props: PageProps) {
  const [sub, setSub] = useState<Sub>("agents");
  const [op, setOp] = useOperator();
  const [draft, setDraft] = useState(op);
  return (
    <div className="page">
      <h1>{c("nav.observability")}</h1>
      <div className="form-row operator">
        <strong>{c("obs.operator")}</strong>
        <input placeholder={c("obs.operator_name")} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <input type="password" placeholder={c("obs.operator_token")} value={draft.token} onChange={(e) => setDraft({ ...draft, token: e.target.value })} />
        <button className="secondary" onClick={() => setOp(draft)}>{c("obs.save")}</button>
      </div>
      <nav className="subtabs">
        {(["agents", "traces", "logs", "humans", "safety", "quality"] as Sub[]).map((k) => (
          <button key={k} className={sub === k ? "active" : ""} onClick={() => setSub(k)}>{c(`obs.${k}`)}</button>
        ))}
      </nav>
      {sub === "agents" && <Agents {...props} op={op} />}
      {sub === "traces" && <Traces {...props} />}
      {sub === "logs" && <Logs />}
      {sub === "safety" && <Safety {...props} op={op} />}
      {sub === "quality" && <Quality {...props} />}
      {sub === "humans" && <Humans {...props} />}
    </div>
  );
}

async function control(op: { name: string; token: string }, path: string, extra: object = {}) {
  const reason = window.prompt(c("obs.reason"));
  if (!reason) return;
  await api(GATEWAY_URL, path, {
    method: "POST",
    headers: { "X-VL-Operator-Token": op.token },
    json: { actor: op.name || "operator", reason, ...extra },
  });
}

function Agents({ tick, refresh, op }: PageProps & { op: { name: string; token: string } }) {
  const [agents] = useLoad(() => api<AgentObservation[]>(GATEWAY_URL, "/observability/agents"), [tick], []);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="table-scroll">
      <table className="agents">
        <thead>
          <tr><th /><th>{c("obs.agent")}</th><th>{c("obs.kind")}</th><th>{c("obs.status")}</th><th>{c("obs.trust")}</th><th>{c("obs.calls")}</th><th>{c("obs.success")}</th><th>{c("obs.p95")}</th><th>{c("obs.quality_score")}</th><th>{c("obs.last_seen")}</th><th /></tr>
        </thead>
        <tbody>
          {agents.map((o) => {
            const a = o.agent;
            const m = o.metrics;
            const hosted = a.kind === "core" || a.kind === "specialist";
            return (
              <Fragment key={a.agent_id}>
                <tr className={o.effective_status !== "active" ? "dim" : ""}>
                  <td>{o.live ? <span className="live-dot" title={c("obs.live")} /> : <span className="idle-dot" />}</td>
                  <td>
                    <strong>{a.display_name || a.agent_id}</strong>
                    <div className="muted small"><code>{a.agent_id}</code> · {a.on_behalf_of}{a.host ? ` · @${a.host}` : ""}</div>
                    <div className="muted small"><code>{a.key_fingerprint}</code></div>
                  </td>
                  <td>{a.kind}</td>
                  <td><Pill status={o.effective_status} />{a.status_reason && <div className="muted small">{a.status_reason}</div>}</td>
                  <td>
                    <button className="score" onClick={() => setOpen(open === a.agent_id ? null : a.agent_id)}>
                      <span className={`trust t${Math.floor(o.trust_score / 25)}`}>{o.trust_score}</span>
                    </button>
                  </td>
                  <td>{m.calls}{m.calls_last_5m ? <span className="muted small"> ({m.calls_last_5m}/5m)</span> : null}</td>
                  <td>{m.calls ? `${Math.round((m.success_rate ?? 1) * 100)}%` : "–"}</td>
                  <td>{m.p95_ms != null ? `${m.p95_ms} ms` : "–"}</td>
                  <td>{o.quality_score ?? "–"}</td>
                  <td className="small">{a.last_seen ? new Date(a.last_seen).toLocaleTimeString() : "–"}</td>
                  <td className="actions">
                    <div className="row-actions">
                    {/* one visible action for the current state; the rest behind "⋯" */}
                    {o.effective_status === "active" ? (
                      <button className="secondary" onClick={() => void run(() => control(op, `/control/agents/${a.agent_id}/suspend`))}>{c("obs.actions.suspend")}</button>
                    ) : a.status !== "revoked" ? (
                      <button onClick={() => void run(() => control(op, `/control/agents/${a.agent_id}/resume`))}>{c("obs.actions.resume")}</button>
                    ) : null}
                    {a.status !== "revoked" && (
                      <details className="menu">
                        <summary aria-label={c("obs.actions_more")} title={c("obs.actions_more")}><Icon name="more" /></summary>
                        <div className="menu-items" onClick={(e) => ((e.currentTarget.parentElement as HTMLDetailsElement).open = false)}>
                          {o.effective_status === "active" && (
                            <button onClick={() => void run(() => control(op, `/control/agents/${a.agent_id}/quarantine`))}>{c("obs.actions.quarantine")}</button>
                          )}
                          {!hosted && (
                            <>
                              <button onClick={() => void run(() => control(op, `/control/agents/${a.agent_id}/rotate_key`))}>{c("obs.actions.rotate_key")}</button>
                              <button onClick={() => void run(() => control(op, `/control/agents/${a.agent_id}/end_sessions`))}>{c("obs.actions.end_sessions")}</button>
                            </>
                          )}
                          <button className="danger" onClick={() => void run(() => control(op, `/control/agents/${a.agent_id}/revoke`))}>{c("obs.actions.revoke")}</button>
                        </div>
                      </details>
                    )}
                    </div>
                  </td>
                </tr>
                {open === a.agent_id && (
                  <tr key={`${a.agent_id}-why`} className="factors">
                    <td />
                    <td colSpan={10}>
                      <strong>{c("obs.factors")}:</strong>{" "}
                      {o.trust_factors.length ? o.trust_factors.map((f) => (
                        <span key={f.name} className="chip">{f.name} {f.impact > 0 ? "+" : ""}{f.impact} · {f.detail}</span>
                      )) : <span className="muted">{c("obs.no_deductions")}</span>}
                      {Object.keys(m.decisions ?? {}).length > 0 && (
                        <> · <strong>{c("obs.decisions")}:</strong> {Object.entries(m.decisions ?? {}).map(([k, v]) => <span key={k}><Pill status={k} /> {v} </span>)}</>
                      )}
                      {" · "}rate limit {a.rate_limit_per_min}/min{a.permissions?.length ? ` · permissions: ${a.permissions.join(", ")}` : ""}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      </div>
    </>
  );
}

function Traces({ tick }: PageProps) {
  const [traces] = useLoad(() => api<TraceSummary[]>(GATEWAY_URL, "/observability/traces"), [tick], []);
  const [view, setView] = useState<TraceView | null>(null);
  if (!traces.length) return <p className="empty">{c("obs.no_traces")}</p>;
  return (
    <div className="two-col traces">
      <ul className="trace-list">
        {traces.map((t) => (
          <li key={t.trace_id} className={view?.summary.trace_id === t.trace_id ? "selected" : ""}
            onClick={() => void api<TraceView>(GATEWAY_URL, `/observability/traces/${t.trace_id}`).then(setView)}>
            <div><Pill status={t.outcome} /> <code>{shortHash(t.trace_id)}</code> · {t.duration_ms} ms · {t.events} {c("obs.events")}</div>
            <div className="muted small">{new Date(t.started).toLocaleTimeString()} · {t.agents.join(" → ")}</div>
          </li>
        ))}
      </ul>
      <div>{view && <Waterfall view={view} />}</div>
    </div>
  );
}

function Waterfall({ view }: { view: TraceView }) {
  const start = new Date(view.summary.started).getTime();
  const total = Math.max(view.summary.duration_ms, 1);
  return (
    <div className="waterfall">
      <h3><code>{view.summary.trace_id}</code></h3>
      {view.events.map((e) => {
        const left = ((new Date(e.ts).getTime() - start) / total) * 100;
        const width = Math.max(((e.latency_ms ?? 0) / total) * 100, 0.8);
        return (
          <div key={e.id} className="wf-row">
            <div className="wf-label"><span className={`etype ${e.type}`}>{e.type}</span> <strong>{e.agent_id}</strong> {e.action}{e.target ? ` → ${e.target}` : ""}</div>
            <div className="wf-track">
              <div className={`wf-bar ${e.outcome}`} style={{ left: `${Math.min(left, 99)}%`, width: `${Math.min(width, 100 - left)}%` }}
                title={`${e.latency_ms ?? 0} ms ${JSON.stringify(e.attrs)}`} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Logs() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agent, setAgent] = useState("");
  const [type, setType] = useState("");
  const [follow, setFollow] = useState(true);
  const followRef = useRef(follow);
  followRef.current = follow;

  useEffect(() => {
    void api<AgentEvent[]>(GATEWAY_URL, "/events/recent?limit=300").then(setEvents);
    const source = new EventSource(`${GATEWAY_URL}/events/stream`);
    source.addEventListener("agent_event", (m) => {
      if (!followRef.current) return;
      const e = JSON.parse((m as MessageEvent).data) as AgentEvent;
      setEvents((prev) => [e, ...prev].slice(0, 500));
    });
    return () => source.close();
  }, []);

  const shown = useMemo(() => events.filter((e) => (!agent || e.agent_id.includes(agent) || e.target?.includes(agent)) && (!type || e.type === type)), [events, agent, type]);
  return (
    <>
      <div className="form-row">
        <label>{c("obs.filter_agent")} <input value={agent} onChange={(e) => setAgent(e.target.value)} /></label>
        <label>{c("obs.filter_type")}
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">{c("obs.all")}</option>
            {["call", "decision", "login", "permission_check", "screening", "control", "security", "audit"].map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> {c("obs.follow")}</label>
      </div>
      <table className="logs">
        <tbody>
          {shown.map((e) => (
            <tr key={e.id} className={e.outcome}>
              <td className="small">{new Date(e.ts).toLocaleTimeString()}</td>
              <td><span className={`etype ${e.type}`}>{e.type}</span></td>
              <td><code>{e.agent_id}</code>{e.actor && <div className="muted small">by {e.actor}</div>}</td>
              <td>{e.action}{e.target ? <span className="muted"> → {e.target}</span> : null}</td>
              <td><Pill status={e.outcome ?? "ok"} /></td>
              <td className="small">{e.latency_ms != null ? `${Math.round(e.latency_ms)} ms` : ""}</td>
              <td className="small muted">{e.on_behalf_of}</td>
              <td className="small"><code>{shortHash(e.trace_id)}</code></td>
              <td className="small muted">{Object.entries(e.attrs ?? {}).map(([k, v]) => `${k}=${v}`).join(" ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Safety({ tick, refresh, op }: PageProps & { op: { name: string; token: string } }) {
  const [s] = useLoad(() => api<SafetySummary>(GATEWAY_URL, "/observability/safety"), [tick], null);
  const [events] = useLoad(() => api<AgentEvent[]>(GATEWAY_URL, "/events/recent?type=security&limit=50"), [tick], []);
  const [error, setError] = useState("");
  if (!s) return null;
  const toggle = async (tenant: string, frozen: boolean) => {
    setError("");
    try {
      await control(op, `/control/tenants/${tenant}/${frozen ? "unfreeze" : "freeze"}`);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="tiles">
        {Object.entries(s.security_events).map(([k, v]) => (
          <div key={k} className="tile alert"><div className="tile-num">{v}</div><Pill status={k} /></div>
        ))}
        <div className="tile"><div className="tile-num">{s.screening_blocked}</div>{c("obs.screening_blocked")}</div>
        <div className="tile"><div className="small muted">{c("obs.by_status")}</div>
          {Object.entries(s.agents_by_status).map(([k, v]) => <div key={k}><Pill status={k} /> {v}</div>)}
        </div>
        <div className="tile"><div className="small muted">{c("obs.quarantined")}</div>{s.quarantined_subjects.map((q) => <code key={q}>{q}</code>)}</div>
      </div>
      <h2>{c("obs.frozen")}</h2>
      <div className="form-row">
        {["vendor", "retailer"].map((tenant) => {
          const frozen = s.frozen.includes(tenant);
          return (
            <button key={tenant} className={frozen ? "danger" : "secondary"} onClick={() => void toggle(tenant, frozen)}>
              {frozen ? <><Icon name="lock" /> {tenant} — {c("obs.unfreeze")}</> : `${c("obs.freeze")} ${tenant}`}
            </button>
          );
        })}
      </div>
      <table>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="alert-row">
              <td className="small">{new Date(e.ts).toLocaleTimeString()}</td>
              <td><Pill status={e.action} /></td>
              <td><code>{e.agent_id}</code></td>
              <td className="small">{String(e.attrs?.detail ?? "")}</td>
              <td className="small"><code>{e.target}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Quality({ tick }: PageProps) {
  const [rows] = useLoad(() => api<QualityRow[]>(GATEWAY_URL, "/observability/quality"), [tick], []);
  return (
    <table>
      <thead>
        <tr><th>{c("obs.specialist")}</th><th>{c("obs.calls")}</th><th>{c("obs.needs_review")}</th><th>{c("obs.errors")}</th><th>{c("obs.quality_score")}</th><th>{c("obs.top_flags")}</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.agent_id}>
            <td><code>{r.agent_id}</code></td>
            <td>{r.calls}</td>
            <td>{r.needs_review_rate != null ? `${Math.round(r.needs_review_rate * 100)}%` : "–"}</td>
            <td>{Math.round(r.error_rate * 100)}%</td>
            <td>{r.quality_score != null ? <span className={`trust t${Math.floor(r.quality_score / 25)}`}>{r.quality_score}</span> : "–"}</td>
            <td>{Object.entries(r.top_flags).map(([k, v]) => <span key={k} className="flag">{k} ×{v}</span>)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Humans({ tick }: PageProps) {
  const [h] = useLoad(() => api<HumanLoopSummary>(GATEWAY_URL, "/observability/humans"), [tick], null);
  if (!h) return null;
  return (
    <>
      <div className="tiles">
        <div className="tile"><div className="tile-num">{h.open}</div>{c("obs.hitl.open")}</div>
        <div className="tile"><div className="tile-num">{h.answered}</div>{c("obs.hitl.answered")}</div>
        <div className={`tile ${h.expired ? "alert" : ""}`}><div className="tile-num">{h.expired}</div>{c("obs.hitl.expired")}</div>
        <div className="tile"><div className="tile-num">{h.median_minutes_to_answer ?? "–"}</div>{c("obs.hitl.median")} (min)</div>
        <div className="tile"><div className="small muted">{c("obs.hitl.by_channel")}</div>{Object.entries(h.by_channel).map(([k, v]) => <div key={k}><Pill status={k} /> {v}</div>)}</div>
        <div className="tile"><div className="small muted">{c("obs.hitl.by_role")}</div>{Object.entries(h.by_role).map(([k, v]) => <div key={k}>{k}: {v}</div>)}</div>
      </div>
      <table className="logs">
        <tbody>
          {h.recent.map((e) => (
            <tr key={e.id}>
              <td className="small">{new Date(e.ts).toLocaleTimeString()}</td>
              <td><span className={`etype ${e.type}`}>{e.type}</span></td>
              <td><code>{e.agent_id}</code></td>
              <td className="small">{String(e.attrs?.role ?? "")} · {String(e.attrs?.task_kind ?? "")}</td>
              <td className="small">{String(e.attrs?.channels ?? e.attrs?.via ?? "")}</td>
              <td><Pill status={String(e.attrs?.status ?? "")} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
