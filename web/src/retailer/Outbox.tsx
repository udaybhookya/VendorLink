import { useState } from "react";
import type { OutboxMessage, TickOut } from "../types/generated";
import { api, RETAILER_URL, VENDOR_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";

/** Local channel fallback (VL_CHANNELS=local): read what would go to Slack / Gmail and answer
 *  as that person, then run a Liaison pass to see the waiting work resume. */
export function Outbox({ tick, refresh }: PageProps) {
  const [retailerBox] = useLoad(() => api<OutboxMessage[]>(RETAILER_URL, "/dev/outbox"), [tick], []);
  const [vendorBox] = useLoad(() => api<OutboxMessage[]>(VENDOR_URL, "/dev/outbox"), [tick], []);
  const [text, setText] = useState<Record<string, string>>({});
  const [last, setLast] = useState("");
  const all = [...retailerBox.map((m) => ({ m, base: RETAILER_URL })), ...vendorBox.map((m) => ({ m, base: VENDOR_URL }))]
    .sort((a, b) => b.m.sent_at.localeCompare(a.m.sent_at));

  async function runTick() {
    const [r, v] = await Promise.all([
      api<TickOut>(RETAILER_URL, "/dev/tick", { method: "POST" }),
      api<TickOut>(VENDOR_URL, "/dev/tick", { method: "POST" }),
    ]);
    setLast(`answered ${(r.answered ?? []).length + (v.answered ?? []).length} · reminded ${(r.reminded ?? []).length + (v.reminded ?? []).length} · escalated ${(r.escalated ?? []).length} · expired ${(r.expired ?? []).length + (v.expired ?? []).length}`);
    refresh();
  }

  return (
    <div className="page">
      <h1>{c("outbox.title")}</h1>
      <p className="muted">{c("outbox.intro")}</p>
      <div className="form-row"><button onClick={() => void runTick()}>▶ Liaison tick</button>{last && <span className="muted">{last}</span>}</div>
      {!all.length && <p className="empty">{c("outbox.none")}</p>}
      {all.map(({ m, base }) => (
        <article key={m.id} className="card">
          <header>
            <Pill status={m.message.channel} /> <strong>{m.message.to}</strong>
            {m.message.subject && <span className="muted"> · {m.message.subject}</span>}
            <span className="muted small"> · {new Date(m.sent_at).toLocaleTimeString()}</span>
          </header>
          <pre className="message">{m.message.text}</pre>
          {(m.replies ?? []).map((r) => <p key={r.message_id} className="reply">↩ <strong>{r.by}</strong>: {r.text}</p>)}
          <div className="form-row">
            <input placeholder={c("outbox.placeholder")} value={text[m.id] ?? ""} onChange={(e) => setText({ ...text, [m.id]: e.target.value })} />
            <button disabled={!text[m.id]} onClick={() => void api(base, `/dev/outbox/${m.message.channel}/${m.id}/reply`, {
              method: "POST", json: { text: text[m.id], by: m.message.channel === "gmail" ? m.message.to : "U-dev" },
            }).then(() => { setText({ ...text, [m.id]: "" }); refresh(); })}>{c("outbox.reply")}</button>
          </div>
        </article>
      ))}
    </div>
  );
}
