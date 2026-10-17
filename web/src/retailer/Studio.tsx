import { useEffect, useState } from "react";
import type { AgentStatus, ConfigPreset, RetailerPolicy } from "../types/generated";
import { api, RETAILER_URL, shortHash } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, useLoad } from "./Console";

export function Studio({ retailer, active, tick, refresh }: PageProps) {
  const [presets] = useLoad(() => api<ConfigPreset[]>(RETAILER_URL, "/config/presets"), [], []);
  const [agents] = useLoad(() => api<AgentStatus[]>(RETAILER_URL, "/registry/agents"), [], []);
  const cards = agents.map((a) => a.card);
  const [policy, setPolicy] = useState<RetailerPolicy | null>(null);
  const [error, setError] = useState("");

  useEffect(() => setPolicy(active?.retailer_policy ?? null), [active, tick]);

  async function activate(body: object) {
    setError("");
    try {
      await api(RETAILER_URL, `/retailers/${retailer.id}/config/activate`, { method: "POST", json: body });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function savePolicy() {
    if (!policy || !active) return;
    setError("");
    try {
      await api(RETAILER_URL, `/config/retailer_policies/${policy.id}`, { method: "PUT", json: policy });
      const refs = { ...active.refs, retailer_policy: { id: policy.id, version: null } };
      await activate({ refs });
    } catch (e) {
      setError(String(e));
    }
  }

  const resolve = (capability: string) => {
    const prefix = capability.endsWith(".*") ? capability.slice(0, -1) : null;
    return cards.filter(
      (card) => active?.specialists.includes(card.agent_id) &&
        card.skills.some((s) => (prefix ? s.capability.startsWith(prefix) : s.capability === capability)),
    );
  };

  return (
    <div className="page">
      <h1>{c("nav.studio")}</h1>
      {error && <div className="error">{error}</div>}
      <h2>{c("studio.presets")}</h2>
      <div className="tiles">
        {presets.map((p) => {
          const isActive = active?.refs.business_profile.id === p.refs.business_profile.id &&
            active?.refs.retailer_profile.id === p.refs.retailer_profile.id;
          return (
            <div key={p.id} className={`tile preset ${isActive ? "selected" : ""}`}>
              <div className="tile-num">{p.id}</div>
              <div>{p.label}</div>
              <button disabled={isActive} onClick={() => void activate({ preset: p.id })}>
                {isActive ? c("studio.active") : c("studio.activate")}
              </button>
            </div>
          );
        })}
      </div>

      {active && (
        <>
          <p>
            {c("studio.hash")}: <code>{shortHash(active.config_hash)}</code>
          </p>
          <h2>{c("studio.specialists")}</h2>
          <div className="chips">{active.specialists.map((s) => <span key={s} className="chip">{s}</span>)}</div>

          <h2>{c("studio.workflows")}</h2>
          <table>
            <tbody>
              {active.workflows.workflows.map((w) => (
                <tr key={w.job_type}>
                  <th>{w.job_type}</th>
                  <td>
                    {w.steps.map((s) => (
                      <span key={s.capability} className="step">
                        <code>{s.capability}{s.optional ? "?" : ""}</code> →{" "}
                        {resolve(s.capability).map((card) => card.agent_id).join(", ") || <em className="muted">{c("studio.skipped")}</em>}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {policy && (
            <>
              <h2>{c("studio.policy")} · v{policy.version}</h2>
              <div className="form-grid">
                <label>
                  auto_order_max_value ({active.country_pack.currency})
                  <input value={policy.auto_order_max_value} onChange={(e) => setPolicy({ ...policy, auto_order_max_value: e.target.value })} />
                </label>
                <label>
                  auto_accept_counter_max_delta
                  <input type="number" step="0.01" min="0" max="1" value={policy.auto_accept_counter_max_delta}
                    onChange={(e) => setPolicy({ ...policy, auto_accept_counter_max_delta: Number(e.target.value) })} />
                </label>
                <label>
                  listing_publish_min_confidence
                  <input type="number" step="0.05" min="0" max="1" value={policy.listing_publish_min_confidence ?? 0.8}
                    onChange={(e) => setPolicy({ ...policy, listing_publish_min_confidence: Number(e.target.value) })} />
                </label>
                <label className="check">
                  <input type="checkbox" checked={policy.new_vendor_needs_human ?? true}
                    onChange={(e) => setPolicy({ ...policy, new_vendor_needs_human: e.target.checked })} />
                  new_vendor_needs_human
                </label>
              </div>
              <button onClick={() => void savePolicy()}>{c("studio.save_policy")}</button>
            </>
          )}
        </>
      )}

      <h2>{c("studio.cards")}</h2>
      <table>
        <thead>
          <tr><th>{c("studio.agent")}</th><th>{c("studio.kind")}</th><th>{c("studio.skills_io")}</th><th>{c("studio.runs")}</th></tr>
        </thead>
        <tbody>
          {agents.map(({ card, route }) => {
            const loaded = card.kind === "core" || active?.specialists.includes(card.agent_id);
            return (
              <tr key={card.agent_id} className={loaded && route ? "" : "dim"}>
                <td>{card.name}<div className="muted small"><code>{card.agent_id}</code> · {card.tenant}</div></td>
                <td>{card.kind}</td>
                <td>
                  {card.skills.map((s) => (
                    <div key={s.capability}>
                      <code>{s.capability}</code> <span className="muted small">{s.input_schema} → {s.output_schema}</span>
                      {s.internal && <span className="chip">{c("studio.internal")}</span>}
                    </div>
                  ))}
                </td>
                <td>{route ?? <span className="muted">{c("studio.other_tenant")}</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
