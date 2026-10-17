import { useState } from "react";
import type { QueueItem } from "../types/generated";
import { api, RETAILER_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";

export function Queue({ retailer, tick, refresh }: PageProps) {
  const [items] = useLoad(() => api<QueueItem[]>(RETAILER_URL, `/retailers/${retailer.id}/queue`), [tick], []);
  const [error, setError] = useState("");
  async function act(item: QueueItem, action: "approve" | "reject") {
    setError("");
    try {
      await api(RETAILER_URL, `/retailers/${retailer.id}/queue/${item.id}/${action}`, { method: "POST" });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <div className="page">
      <h1>{c("nav.queue")}</h1>
      {error && <div className="error">{error}</div>}
      {!items.length && <p className="empty">{c("queue.none")}</p>}
      <table>
        <tbody>
          {items.map((q) => (
            <tr key={q.id}>
              <td><Pill status={q.kind} /></td>
              <td><code>{q.ref_id}</code></td>
              <td>{q.reason}</td>
              <td><Pill status={q.status ?? "pending"} /></td>
              <td className="actions">
                {q.status === "pending" && (
                  <>
                    <button onClick={() => void act(q, "approve")}>{c("queue.approve")}</button>
                    <button className="secondary" onClick={() => void act(q, "reject")}>{c("queue.reject")}</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
