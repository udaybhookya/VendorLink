import { useState } from "react";
import type { DirectoryEntry, Listing, QueueItem, RetailerVendor, TermsResponse } from "../types/generated";
import { api, RETAILER_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Icon } from "../lib/icons";

/** Connect with suppliers: invite from the directory, review applications, work with the connected. */
export function Suppliers({ retailer, tick, refresh }: PageProps) {
  const base = `/retailers/${retailer.id}`;
  const [vendors] = useLoad(() => api<RetailerVendor[]>(RETAILER_URL, `${base}/vendors`), [tick], []);
  const [directory] = useLoad(() => api<DirectoryEntry[]>(RETAILER_URL, `${base}/directory`), [tick], []);
  const [listings] = useLoad(() => api<Listing[]>(RETAILER_URL, `${base}/listings`), [tick], []);
  const [queue] = useLoad(() => api<QueueItem[]>(RETAILER_URL, `${base}/queue`), [tick], []);
  const [termsAnswers] = useLoad(() => api<TermsResponse[]>(RETAILER_URL, `${base}/terms-responses`), [tick], []);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setError("");
    try {
      await fn();
      setNote(done);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };
  const pending = vendors.filter((v) => v.status === "pending_approval" || v.status === "checks_failed");
  const connected = vendors.filter((v) => v.status === "approved");
  const known = new Set(vendors.map((v) => v.vendor_id));
  const invitable = directory.filter((d) => !known.has(d.party_id));

  return (
    <div className="page">
      <h1>{c("nav.suppliers")}</h1>
      {error && <div className="error">{error}</div>}
      {note && <div className="note">{note}</div>}

      <h2>{c("suppliers.applications")} ({pending.length})</h2>
      {!pending.length && <p className="empty">{c("suppliers.none")}</p>}
      {pending.map((v) => {
        const item = queue.find((q) => q.kind === "vendor" && q.ref_id === v.vendor_id && q.status === "pending");
        return (
          <article key={v.vendor_id} className="card">
            <header>
              <strong>{v.display_name}</strong> <code>{v.vendor_id}</code> · {v.country} · {v.business_profile} <Pill status={v.status} />
            </header>
            <ul className="checks">
              {(v.checks ?? []).map((ch, i) => (
                <li key={i} className={ch.status}><Icon name={ch.status === "pass" ? "check" : "x"} size={15} /> {ch.doc_type}: <code>{ch.code}</code> {ch.detail}</li>
              ))}
            </ul>
            {item && (
              <div className="actions">
                <button onClick={() => void act(() => api(RETAILER_URL, `${base}/queue/${item.id}/approve`, { method: "POST" }), `${v.display_name} approved`)}>{c("suppliers.approve")}</button>
                <button className="secondary" onClick={() => void act(() => api(RETAILER_URL, `${base}/queue/${item.id}/reject`, { method: "POST" }), `${v.display_name} rejected`)}>{c("suppliers.reject")}</button>
              </div>
            )}
          </article>
        );
      })}

      <h2>{c("suppliers.connected")} ({connected.length})</h2>
      {connected.map((v) => (
        <article key={v.vendor_id} className="card">
          <header>
            <strong>{v.display_name}</strong> <code>{v.vendor_id}</code> · {v.country} <Pill status={v.status} />
            {termsAnswers.filter((a) => a.vendor_id === v.vendor_id).slice(-1).map((a) => (
              <span key={a.terms_id} className="muted small"> · {c("suppliers.terms_answer")} <Pill status={a.accepted ? "approved" : "rejected"} /> {a.reason}</span>
            ))}
            <span className="spacer" />
            <button className="secondary" onClick={() => void act(async () => {
              const r = await api<{ accepted: boolean; detail: string }>(RETAILER_URL, `${base}/terms/${v.vendor_id}`, { method: "POST" });
              setNote(`Terms → ${v.display_name}: ${r.accepted ? "accepted" : "not accepted"} (${r.detail})`);
            }, "")}>{c("suppliers.terms")}</button>
          </header>
          <ul className="list">
            {listings.filter((l) => l.vendor_id === v.vendor_id).map((l) => (
              <li key={l.id}>
                <Pill status={l.status ?? "draft"} /> {l.sku_name} · {l.qty} {l.unit} @ {l.currency} {l.price}
                {(l.flags ?? []).map((f) => <span key={f.code} className="flag">{f.code}</span>)}
              </li>
            ))}
          </ul>
        </article>
      ))}

      <h2>{c("suppliers.directory")} ({invitable.length})</h2>
      <table>
        <tbody>
          {invitable.map((d) => (
            <tr key={d.party_id}>
              <td><strong>{d.display_name}</strong></td>
              <td><code>{d.party_id}</code></td>
              <td><code>{d.agent_id}</code></td>
              <td className="actions">
                <button onClick={() => void act(() => api(RETAILER_URL, `${base}/suppliers/${d.party_id}/invite`, {
                  method: "POST", json: { message: c("suppliers.invite_message") },
                }), `${c("suppliers.invited")}: ${d.display_name}`)}>{c("suppliers.invite")}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
