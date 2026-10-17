import { useState } from "react";
import type { HumanTask, InfoResponseMessage, JobRun, RetailerVendor } from "../types/generated";
import { api, RETAILER_URL, shortHash } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";

const tomorrow = () => new Date(Date.now() + 86400000).toISOString().slice(0, 10);

/** People in the loop: what agents are waiting on, batch jobs, and suppliers' answers. */
export function Tasks({ retailer, tick, refresh }: PageProps) {
  const base = `/retailers/${retailer.id}`;
  const [tasks] = useLoad(() => api<HumanTask[]>(RETAILER_URL, `${base}/tasks`), [tick], []);
  const [jobs] = useLoad(() => api<JobRun[]>(RETAILER_URL, `${base}/jobs`), [tick], []);
  const [answers] = useLoad(() => api<InfoResponseMessage[]>(RETAILER_URL, `${base}/info-responses`), [tick], []);
  const [vendors] = useLoad(() => api<RetailerVendor[]>(RETAILER_URL, `${base}/vendors`), [tick], []);
  const approved = vendors.filter((v) => v.status === "approved");
  const [picked, setRun] = useState({ for_date: tomorrow(), vendor_id: "" });
  const run = { ...picked, vendor_id: picked.vendor_id || approved[0]?.vendor_id || "" };
  const [text, setText] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };
  const answer = (t: HumanTask, value: string) =>
    act(() => api(RETAILER_URL, `${base}/tasks/${t.id}/answer`, { method: "POST", json: { text: value, by: "console" } }));
  const open = tasks.filter((t) => t.status === "open");
  const closed = tasks.filter((t) => t.status !== "open" && t.kind !== "notice").slice(0, 20);

  return (
    <div className="page">
      <h1>{c("nav.tasks")}</h1>
      {error && <div className="error">{error}</div>}

      <h2>{c("tasks.open")} ({open.length})</h2>
      {!open.length && <p className="empty">{c("tasks.none")}</p>}
      {open.map((t) => (
        <article key={t.id} className="card">
          <header>
            <Pill status={t.kind} /> <strong>{t.role}</strong> <code>{t.token}</code>
            <span className="muted small">{t.subject_ref}{t.deadline ? ` · until ${new Date(t.deadline).toLocaleString()}` : ""}</span>
          </header>
          <p>{t.question}</p>
          <p className="muted small">
            {c("tasks.deliveries")}: {(t.deliveries ?? []).map((d) => `${d.channel} → ${d.to}`).join(" · ")}
            {t.escalation_level ? ` · step ${t.escalation_level + 1}` : ""}
          </p>
          {(t.request.choices ?? []).length ? (
            <div className="actions">
              {(t.request.choices ?? []).map((ch) => <button key={ch.id} onClick={() => void answer(t, ch.id)}>{ch.label}</button>)}
            </div>
          ) : (
            <div className="form-row">
              <input value={text[t.id] ?? ""} onChange={(e) => setText({ ...text, [t.id]: e.target.value })} />
              <button disabled={!text[t.id]} onClick={() => void answer(t, text[t.id] ?? "")}>{c("tasks.answer")}</button>
            </div>
          )}
        </article>
      ))}

      <h2>{c("tasks.jobs")}</h2>
      <div className="form-row">
        <label>{c("tasks.for_date")} <input type="date" value={run.for_date} onChange={(e) => setRun({ ...run, for_date: e.target.value })} /></label>
        <label>
          {c("tasks.vendor")}{" "}
          <select value={run.vendor_id} onChange={(e) => setRun({ ...run, vendor_id: e.target.value })}>
            {approved.map((v) => <option key={v.vendor_id} value={v.vendor_id}>{v.display_name}</option>)}
          </select>
        </label>
        <button disabled={!run.vendor_id} onClick={() => void act(() => api(RETAILER_URL, `${base}/jobs/replenishment-run`, { method: "POST", json: run }))}>{c("tasks.run")}</button>
      </div>
      <table>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td><code>{j.id}</code></td>
              <td><Pill status={j.status} /></td>
              <td className="small">{new Date(j.started_at).toLocaleString()}</td>
              <td className="small">
                {((j.summary?.orders as { store: string; status: string }[]) ?? []).map((o) => <span key={o.store} className="chip">{o.store}: {o.status}</span>)}
                {(j.waiting_on ?? []).length > 0 && <span className="muted"> · {c("tasks.waiting_on")} {(j.waiting_on ?? []).length}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>{c("tasks.responses")}</h2>
      <ul className="list">
        {answers.map((a) => <li key={a.request_id}><code>{a.vendor_id}</code> · {a.request_id}: <strong>{a.answer || a.status}</strong></li>)}
      </ul>

      <h2>{c("tasks.done")}</h2>
      <table>
        <tbody>
          {closed.map((t) => (
            <tr key={t.id}>
              <td><Pill status={t.status ?? "answered"} /></td>
              <td>{t.role}</td>
              <td className="small">{t.question.slice(0, 90)}</td>
              <td><strong>{String(t.answer_value ?? t.answer ?? "")}</strong></td>
              <td className="small muted">{t.answered_via ? `${c("tasks.via")} ${t.answered_via}` : ""} {t.answered_by ?? ""}</td>
              <td className="small"><code>{shortHash(t.trace_id)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
