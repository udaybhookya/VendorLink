import { useState } from "react";
import type { AgentCredential, AuditRecord, AuditVerifyResult, ConsentRecord, OrderMandate, SecurityEvent } from "../types/generated";
import { api, GATEWAY_URL, shortHash } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Icon } from "../lib/icons";

export function Trust({ tick, refresh }: PageProps) {
  const [creds] = useLoad(() => api<AgentCredential[]>(GATEWAY_URL, "/credentials"), [tick], []);
  const [events] = useLoad(() => api<SecurityEvent[]>(GATEWAY_URL, "/security-events"), [tick], []);
  const [consents] = useLoad(() => api<ConsentRecord[]>(GATEWAY_URL, "/consents"), [tick], []);
  const [mandates] = useLoad(() => api<OrderMandate[]>(GATEWAY_URL, "/mandates/order"), [tick], []);
  const [audit] = useLoad(() => api<AuditRecord[]>(GATEWAY_URL, "/audit?limit=200"), [tick], []);
  const [verify, setVerify] = useState<AuditVerifyResult | null>(null);

  async function runVerify() {
    setVerify(await api<AuditVerifyResult>(GATEWAY_URL, "/audit/verify"));
  }
  async function tamper() {
    const seq = Math.max(0, Math.floor(audit.length / 2));
    await api(GATEWAY_URL, `/dev/tamper/${seq}`, { method: "POST" });
    setVerify(null);
    refresh();
  }

  return (
    <div className="page">
      <h1>{c("nav.trust")}</h1>
      <div className="form-row">
        <button onClick={() => void runVerify()}>{c("trust.verify")}</button>
        <button className="secondary" onClick={() => void tamper()}>{c("trust.tamper")}</button>
        {verify && (
          <strong className={verify.ok ? "ok" : "bad"}>
            {verify.ok ? <><Icon name="check" /> {c("trust.verified")} ({verify.checked})</> : <><Icon name="x" /> {c("trust.broken")} #{verify.first_broken_seq}</>}
          </strong>
        )}
      </div>

      <h2>{c("trust.events")}</h2>
      {!events.length && <p className="empty">{c("trust.none")}</p>}
      <table>
        <tbody>
          {events.slice().reverse().map((e) => (
            <tr key={e.id} className="alert-row">
              <td>{new Date(e.ts).toLocaleTimeString()}</td>
              <td><Pill status={e.type} /></td>
              <td><code>{e.agent_id}</code></td>
              <td>{e.evidence}</td>
              <td>{e.action_taken}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>{c("trust.agents")}</h2>
      <table>
        <tbody>
          {creds.map((a) => (
            <tr key={a.agent_id}>
              <td><code>{a.agent_id}</code></td>
              <td>{a.party_type}</td>
              <td>{a.on_behalf_of}</td>
              <td><code>{shortHash(a.public_key)}</code></td>
              <td><Pill status={a.status ?? "active"} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="two-col">
        <div>
          <h2>{c("trust.consents")}</h2>
          <ul className="list">
            {consents.map((x) => (
              <li key={x.id}>
                {x.vendor_id} · “{x.transcript}” ({x.language}) · <code>{shortHash(x.subject_hash)}</code>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2>{c("trust.mandates")}</h2>
          <ul className="list">
            {mandates.map((m) => (
              <li key={m.order_id}>
                <code>{m.order_id}</code> v{m.final_version} · terms <code>{shortHash(m.terms_hash)}</code>
                {m.consent_ref && " · consent ✓"}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <h2>{c("trust.audit")}</h2>
      <div className="table-scroll">
      <table className="audit">
        <thead>
          <tr><th>{c("trust.cols.seq")}</th><th>{c("trust.cols.time")}</th><th>{c("trust.cols.actor")}</th><th>{c("trust.cols.on_behalf_of")}</th><th>{c("trust.cols.action")}</th><th>{c("trust.cols.target")}</th><th>{c("trust.cols.outcome")}</th><th>{c("trust.cols.config")}</th><th>{c("trust.cols.hash")}</th></tr>
        </thead>
        <tbody>
          {audit.slice().reverse().map((r) => (
            <tr key={r.seq} className={verify && !verify.ok && verify.first_broken_seq === r.seq ? "broken" : ""}>
              <td>{r.seq}</td>
              <td>{new Date(r.ts).toLocaleTimeString()}</td>
              <td><code>{r.actor_agent_id}</code></td>
              <td>{r.on_behalf_of}</td>
              <td>{r.action}</td>
              <td><code>{r.target}</code></td>
              <td><Pill status={r.outcome} /></td>
              <td><code>{shortHash(r.config_hash)}</code></td>
              <td><code>{shortHash(r.hash)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}
