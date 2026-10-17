import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type {
  ActivitySummary, ActivityTiming, AgentDetail, AgentEvent, AgentObservation, Connection, HumanLoopSummary,
  ObservabilityOverview, QualityRow, SafetySummary, TraceSummary, TraceView,
} from "../types/generated";
import { api, GATEWAY_URL, shortHash } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Icon } from "../lib/icons";

type Sub = "overview" | "agents" | "activities" | "traces" | "logs" | "safety" | "quality" | "humans";
type Operator = { name: string; token: string };
/** Shared by every tab: who is acting, and how to open one agent's detail. */
type ObsProps = PageProps & { op: Operator; openAgent: (id: string) => void };

const fmtMs = (ms?: number | null) => (ms == null ? "–" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
const fmtN = (n?: number | null) => (n ?? 0).toLocaleString();
const fmtTime = (ts?: string | null) => (ts ? new Date(ts).toLocaleTimeString() : "–");

function useOperator(): [Operator, (v: Operator) => void] {
  const read = () => {
    try {
      return JSON.parse(sessionStorage.getItem("vl.operator") ?? "") as Operator;
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
  const [sub, setSub] = useState<Sub>("overview");
  const [op, setOp] = useOperator();
  const [draft, setDraft] = useState(op);
  const [agent, openAgent] = useState<string | null>(null);
  const p: ObsProps = { ...props, op, openAgent };
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
        {(["overview", "agents", "activities", "traces", "logs", "humans", "safety", "quality"] as Sub[]).map((k) => (
          <button key={k} className={sub === k ? "active" : ""} onClick={() => setSub(k)}>{c(`obs.${k}`)}</button>
        ))}
      </nav>
      {sub === "overview" && <Overview {...p} />}
      {sub === "agents" && <Agents {...p} />}
      {sub === "activities" && <Activities {...p} />}
      {sub === "traces" && <Traces {...props} />}
      {sub === "logs" && <Logs />}
      {sub === "safety" && <Safety {...props} op={op} />}
      {sub === "quality" && <Quality {...props} />}
      {sub === "humans" && <Humans {...props} />}
      {agent && <AgentDrawer {...p} agentId={agent} onClose={() => openAgent(null)} />}
    </div>
  );
}

async function control(op: Operator, path: string, extra: object = {}) {
  const reason = window.prompt(c("obs.reason"));
  if (!reason) return;
  await api(GATEWAY_URL, path, {
    method: "POST",
    headers: { "X-VL-Operator-Token": op.token },
    json: { actor: op.name || "operator", reason, ...extra },
  });
}

/** Run a control action, refresh, and surface a refusal (e.g. a missing operator token). */
function useRun(refresh: () => void): [string, (fn: () => Promise<unknown>) => Promise<void>] {
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
  return [error, run];
}

function Overview({ tick, openAgent }: ObsProps) {
  const [o] = useLoad(() => api<ObservabilityOverview>(GATEWAY_URL, "/observability/overview"), [tick], null);
  const [conns] = useLoad(() => api<Connection[]>(GATEWAY_URL, "/observability/connections"), [tick], []);
  if (!o) return null;
  return (
    <>
      <div className="tiles">
        {(["vendor", "retailer", "hosted"] as const).map((k) => {
          const s = o.sides[k];
          if (!s) return null;
          return (
            <div key={k} className={`tile ${s.blocked ? "alert" : ""}`}>
              <div className="small muted">{c(`obs.sides.${k}`)}</div>
              <div className="tile-num">{s.active ?? 0}<span className="muted small"> / {s.total ?? 0}</span></div>
              <div className="small">{c("obs.side_line", { active: s.active ?? 0, total: s.total ?? 0, live: s.live ?? 0, talking: s.talking ?? 0 })}</div>
              {(s.blocked ?? 0) > 0 && <div className="small"><Pill status="denied" /> {c("obs.blocked_n", { n: s.blocked ?? 0 })}</div>}
            </div>
          );
        })}
        <div className="tile">
          <div className="small muted">{c("obs.messages")}</div>
          <div className="tile-num">{fmtN(o.messages)}</div>
          <div className="small">{c("obs.messages_line", { n: o.connections, p95: fmtMs(o.p95_relay_ms) })}</div>
        </div>
        <div className="tile">
          <div className="small muted">{c("obs.tokens")}</div>
          <div className="tile-num">{fmtN(o.tokens_in + o.tokens_out)}</div>
          <div className="small">{c("obs.tokens_line", { tin: fmtN(o.tokens_in), tout: fmtN(o.tokens_out), calls: fmtN(o.model_calls) })}</div>
        </div>
        <div className="tile">
          <div className="small muted">{c("obs.logins")}</div>
          <div className="tile-num">{fmtN(o.logins)}</div>
        </div>
        <div className={`tile ${o.authn_failures ? "alert" : ""}`}>
          <div className="small muted">{c("obs.authn_failures")}</div>
          <div className="tile-num">{fmtN(o.authn_failures)}</div>
          <div className="small muted">{c("obs.authn_hint")}</div>
        </div>
        <div className={`tile ${o.authz_denials ? "alert" : ""}`}>
          <div className="small muted">{c("obs.authz_denials")}</div>
          <div className="tile-num">{fmtN(o.authz_denials)}</div>
          <div className="small muted">{c("obs.authz_hint")}</div>
        </div>
      </div>
      <h2>{c("obs.conn.title")}</h2>
      <Connections rows={conns} openAgent={openAgent} />
    </>
  );
}

function Connections({ rows, openAgent }: { rows: Connection[]; openAgent: (id: string) => void }) {
  if (!rows.length) return <p className="empty">{c("obs.conn.none")}</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr><th>{c("obs.conn.supplier")}</th><th /><th>{c("obs.conn.retailer")}</th><th>{c("obs.conn.messages")}</th><th>{c("obs.conn.skills")}</th><th>{c("obs.conn.decisions")}</th><th>{c("obs.conn.denied")}</th><th>{c("obs.p95")}</th><th>{c("obs.conn.last")}</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.vendor_agent}|${r.retailer_agent}`} className={r.denied || r.errors ? "alert-row" : ""}>
              <td>
                <button className="link" onClick={() => openAgent(r.vendor_agent)}>{r.vendor_agent}</button>
                <div className="muted small">{r.vendor}</div>
              </td>
              <td className="muted">⇄</td>
              <td>
                <button className="link" onClick={() => openAgent(r.retailer_agent)}>{r.retailer_agent}</button>
                <div className="muted small">{r.retailer}</div>
              </td>
              <td>
                <strong>{r.messages ?? 0}</strong>
                <div className="muted small">{c("obs.conn.direction", { to_retailer: r.to_retailer ?? 0, to_vendor: r.to_vendor ?? 0 })}</div>
              </td>
              <td><div className="chips">{Object.entries(r.skills ?? {}).map(([k, v]) => <span key={k} className="chip">{k} ×{v}</span>)}</div></td>
              <td>{Object.entries(r.decisions ?? {}).map(([k, v]) => <span key={k}><Pill status={k} /> {v} </span>)}</td>
              <td>{r.denied ? <Pill status="denied" /> : null} {r.denied || "–"}</td>
              <td>{fmtMs(r.p95_ms)}</td>
              <td className="small">{fmtTime(r.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One visible action for the agent's current state; the rest behind "⋯". */
function AgentActions({ o, op, run }: { o: AgentObservation; op: Operator; run: (fn: () => Promise<unknown>) => Promise<void> }) {
  const a = o.agent;
  const hosted = a.kind === "core" || a.kind === "specialist";
  const act = (action: string) => () => void run(() => control(op, `/control/agents/${a.agent_id}/${action}`));
  return (
    <div className="row-actions">
      {o.effective_status === "active" ? (
        <button className="secondary" onClick={act("suspend")}>{c("obs.actions.suspend")}</button>
      ) : a.status !== "revoked" ? (
        <button onClick={act("resume")}>{c("obs.actions.resume")}</button>
      ) : null}
      {a.status !== "revoked" && (
        <details className="menu">
          <summary aria-label={c("obs.actions_more")} title={c("obs.actions_more")}><Icon name="more" /></summary>
          <div className="menu-items" onClick={(e) => ((e.currentTarget.parentElement as HTMLDetailsElement).open = false)}>
            {o.effective_status === "active" && <button onClick={act("quarantine")}>{c("obs.actions.quarantine")}</button>}
            {!hosted && (
              <>
                <button onClick={act("rotate_key")}>{c("obs.actions.rotate_key")}</button>
                <button onClick={act("end_sessions")}>{c("obs.actions.end_sessions")}</button>
              </>
            )}
            <button className="danger" onClick={act("revoke")}>{c("obs.actions.revoke")}</button>
          </div>
        </details>
      )}
    </div>
  );
}

function TrustFactors({ o }: { o: AgentObservation }) {
  const m = o.metrics;
  return (
    <>
      <strong>{c("obs.factors")}:</strong>{" "}
      {o.trust_factors.length ? o.trust_factors.map((f) => (
        <span key={f.name} className="chip">{f.name} {f.impact > 0 ? "+" : ""}{f.impact} · {f.detail}</span>
      )) : <span className="muted">{c("obs.no_deductions")}</span>}
      {Object.keys(m.decisions ?? {}).length > 0 && (
        <> · <strong>{c("obs.decisions")}:</strong> {Object.entries(m.decisions ?? {}).map(([k, v]) => <span key={k}><Pill status={k} /> {v} </span>)}</>
      )}
    </>
  );
}

function Agents({ tick, refresh, op, openAgent }: ObsProps) {
  const [agents] = useLoad(() => api<AgentObservation[]>(GATEWAY_URL, "/observability/agents"), [tick], []);
  const [open, setOpen] = useState<string | null>(null);
  const [error, run] = useRun(refresh);
  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="table-scroll">
      <table className="agents">
        <thead>
          <tr><th /><th>{c("obs.agent")}</th><th>{c("obs.kind")}</th><th>{c("obs.status")}</th><th>{c("obs.trust")}</th><th>{c("obs.calls")}</th><th>{c("obs.success")}</th><th>{c("obs.p95")}</th><th>{c("obs.tokens")}</th><th>{c("obs.quality_score")}</th><th>{c("obs.last_seen")}</th><th /></tr>
        </thead>
        <tbody>
          {agents.map((o) => {
            const a = o.agent;
            const m = o.metrics;
            const tokens = (m.tokens_in ?? 0) + (m.tokens_out ?? 0);
            return (
              <Fragment key={a.agent_id}>
                <tr className={o.effective_status !== "active" ? "dim" : ""}>
                  <td>{o.live ? <span className="live-dot" title={c("obs.live")} /> : <span className="idle-dot" />}</td>
                  <td>
                    <button className="link" onClick={() => openAgent(a.agent_id)}><strong>{a.display_name || a.agent_id}</strong></button>
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
                  <td>{fmtMs(m.p95_ms)}</td>
                  <td title={tokens ? c("obs.tokens_line", { tin: fmtN(m.tokens_in), tout: fmtN(m.tokens_out), calls: fmtN(m.model_calls) }) : ""}>{tokens ? fmtN(tokens) : "–"}</td>
                  <td>{o.quality_score ?? "–"}</td>
                  <td className="small">{fmtTime(a.last_seen)}</td>
                  <td className="actions"><AgentActions o={o} op={op} run={run} /></td>
                </tr>
                {open === a.agent_id && (
                  <tr key={`${a.agent_id}-why`} className="factors">
                    <td />
                    <td colSpan={11}>
                      <TrustFactors o={o} />
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

function Timings({ rows }: { rows: ActivityTiming[] }) {
  if (!rows.length) return <p className="empty">{c("obs.act.none")}</p>;
  const slowest = Math.max(...rows.map((r) => r.p95_ms ?? 0), 1);
  return (
    <div className="table-scroll">
      <table className="timings">
        <thead>
          <tr><th>{c("obs.act.activity")}</th><th>{c("obs.act.runs")}</th><th>{c("obs.act.p50")}</th><th>{c("obs.act.p95")}</th><th /><th>{c("obs.act.max")}</th><th>{c("obs.errors")}</th><th>{c("obs.tokens")}</th><th>{c("obs.act.agents")}</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.activity} className={r.errors || r.denied ? "alert-row" : ""}>
              <td><code>{r.activity}</code></td>
              <td>{r.count}</td>
              <td>{fmtMs(r.p50_ms)}</td>
              <td><strong>{fmtMs(r.p95_ms)}</strong></td>
              <td className="bar-cell"><div className="time-bar" style={{ width: `${Math.max(((r.p95_ms ?? 0) / slowest) * 100, 1)}%` }} /></td>
              <td>{fmtMs(r.max_ms)}</td>
              <td>{(r.errors ?? 0) + (r.denied ?? 0) || "–"}</td>
              <td>{(r.tokens_in ?? 0) + (r.tokens_out ?? 0) ? fmtN((r.tokens_in ?? 0) + (r.tokens_out ?? 0)) : "–"}</td>
              <td className="small muted">{(r.agents ?? []).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Activities({ tick }: ObsProps) {
  const [a] = useLoad(() => api<ActivitySummary>(GATEWAY_URL, "/observability/activities"), [tick], null);
  if (!a) return null;
  return (
    <>
      <h2>{c("obs.act.flows")}</h2>
      <p className="muted small">{c("obs.act.flows_hint")}</p>
      <Timings rows={a.flows} />
      <h2>{c("obs.act.steps")}</h2>
      <p className="muted small">{c("obs.act.steps_hint")}</p>
      <Timings rows={a.steps} />
    </>
  );
}

function Counts({ counts }: { counts: Record<string, number> | undefined }) {
  const entries = Object.entries(counts ?? {});
  if (!entries.length) return <span className="muted">{c("obs.detail.none")}</span>;
  return <span className="chips">{entries.map(([k, v]) => <span key={k}><Pill status={k} /> ×{v}</span>)}</span>;
}

function AgentDrawer({ agentId, tick, refresh, op, onClose, openAgent }: ObsProps & { agentId: string; onClose: () => void }) {
  const [d, loadError] = useLoad(() => api<AgentDetail>(GATEWAY_URL, `/observability/agents/${encodeURIComponent(agentId)}`), [agentId, tick], null);
  const [error, run] = useRun(refresh);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const o = d?.observation;
  const a = o?.agent;
  const auth = d?.auth;
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-label={agentId} onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h2>{a?.display_name || agentId}</h2>
            <div className="muted small"><code>{agentId}</code>{a ? ` · ${a.kind} · ${a.on_behalf_of}` : ""}</div>
          </div>
          <button className="secondary" aria-label={c("obs.detail.close")} title={c("obs.detail.close")} onClick={onClose}><Icon name="x" /></button>
        </header>
        {(error || loadError) && <div className="error">{error || loadError}</div>}
        {o && a && auth && d && (
          <>
            <div className="drawer-status">
              {o.live ? <span className="live-dot" title={c("obs.live")} /> : <span className="idle-dot" />}
              <Pill status={o.effective_status} />
              <span className={`trust t${Math.floor(o.trust_score / 25)}`}>{o.trust_score}</span>
              <span className="muted small">{o.metrics.calls} {c("obs.calls").toLowerCase()} · p95 {fmtMs(o.metrics.p95_ms)}</span>
              <AgentActions o={o} op={op} run={run} />
            </div>
            {a.status_reason && <p className="muted small">{a.status_reason}</p>}
            <p className="small"><TrustFactors o={o} /></p>

            <h3>{c("obs.detail.usage")}</h3>
            <p className="small">{c("obs.tokens_line", { tin: fmtN(o.metrics.tokens_in), tout: fmtN(o.metrics.tokens_out), calls: fmtN(o.metrics.model_calls) })}</p>

            <h3>{c("obs.detail.auth")}</h3>
            <dl className="kv">
              <dt>{c("obs.detail.key")}</dt><dd><code>{auth.key_fingerprint || "–"}</code></dd>
              <dt>{c("obs.detail.host")}</dt><dd>{auth.host ? <code>{auth.host}</code> : c("obs.detail.own_key")}</dd>
              <dt>{c("obs.detail.permissions")}</dt><dd>{auth.permissions?.length ? <span className="chips">{auth.permissions.map((p) => <code key={p}>{p}</code>)}</span> : <span className="muted">{c("obs.detail.all_permissions")}</span>}</dd>
              <dt>{c("obs.detail.rate_limit")}</dt><dd>{c("obs.detail.per_min", { n: auth.rate_limit_per_min ?? 0 })}</dd>
              {auth.sessions_valid_after && <><dt>{c("obs.detail.sessions_after")}</dt><dd>{fmtTime(auth.sessions_valid_after)}</dd></>}
              {auth.key_rotation_requested && <><dt>{c("obs.detail.rotation")}</dt><dd><Pill status="pending" /></dd></>}
              <dt>{c("obs.detail.logins")}</dt><dd>{auth.logins ?? 0}{auth.last_login ? <span className="muted small"> · {c("obs.detail.last_login")} {fmtTime(auth.last_login)}</span> : null}</dd>
              <dt>{c("obs.detail.scopes")}</dt><dd><span className="chips">{Object.entries(auth.scopes ?? {}).map(([k, v]) => <span key={k} className="chip">{k} ×{v}</span>)}</span>{!Object.keys(auth.scopes ?? {}).length && <span className="muted">{c("obs.detail.none")}</span>}</dd>
              <dt>{c("obs.detail.authn")}</dt><dd><Counts counts={auth.authn_failures} /></dd>
              <dt>{c("obs.detail.authz")}</dt><dd><Counts counts={auth.authz_denials} /></dd>
            </dl>

            {d.connections.length > 0 && (
              <>
                <h3>{c("obs.detail.talks_with")}</h3>
                <Connections rows={d.connections} openAgent={openAgent} />
              </>
            )}

            <h3>{c("obs.detail.timings")}</h3>
            <Timings rows={d.activities} />

            <h3>{c("obs.detail.logs")}</h3>
            <EventTable events={d.events} />
          </>
        )}
      </aside>
    </div>
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

function EventTable({ events }: { events: AgentEvent[] }) {
  return (
    <div className="table-scroll">
      <table className="logs">
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className={e.outcome}>
              <td className="small">{fmtTime(e.ts)}</td>
              <td><span className={`etype ${e.type}`}>{e.type}</span></td>
              <td><code>{e.agent_id}</code>{e.actor && <div className="muted small">by {e.actor}</div>}</td>
              <td>{e.action}{e.target ? <span className="muted"> → {e.target}</span> : null}</td>
              <td><Pill status={e.outcome ?? "ok"} /></td>
              <td className="small">{e.latency_ms != null ? fmtMs(e.latency_ms) : ""}</td>
              <td className="small muted">{e.on_behalf_of}</td>
              <td className="small"><code>{shortHash(e.trace_id)}</code></td>
              <td className="small muted">{Object.entries(e.attrs ?? {}).map(([k, v]) => `${k}=${v}`).join(" ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
      <EventTable events={shown} />
    </>
  );
}

function Safety({ tick, refresh, op }: PageProps & { op: Operator }) {
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
