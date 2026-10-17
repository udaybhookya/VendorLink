import { useState } from "react";
import type { RetailerOrder, RetailerVendor } from "../types/generated";
import { api, RETAILER_URL, shortHash } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Icon } from "../lib/icons";

const tomorrow = () => new Date(Date.now() + 86400000).toISOString().slice(0, 10);

export function Orders({ retailer, active, tick, refresh }: PageProps) {
  const [orders] = useLoad(() => api<RetailerOrder[]>(RETAILER_URL, `/retailers/${retailer.id}/orders`), [tick], []);
  const [vendors] = useLoad(() => api<RetailerVendor[]>(RETAILER_URL, `/retailers/${retailer.id}/vendors`), [tick], []);
  const approved = vendors.filter((v) => v.status === "approved");
  // Defaults come from the data: the first approved supplier and the first store.
  const [picked, setForm] = useState({ vendor_id: "", store_id: "", for_date: tomorrow() });
  const form = {
    ...picked,
    vendor_id: picked.vendor_id || approved[0]?.vendor_id || "",
    store_id: picked.store_id || retailer.stores?.[0]?.id || "",
  };
  const [error, setError] = useState("");
  const tz = active?.country_pack.timezone;
  const vendorName = (id: string) => vendors.find((v) => v.vendor_id === id)?.display_name ?? id;
  const storeName = (id: string) => retailer.stores?.find((st) => st.id === id)?.name ?? id;

  async function place() {
    setError("");
    try {
      await api(RETAILER_URL, `/retailers/${retailer.id}/orders/place`, { method: "POST", json: form });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  const when = (iso: string) =>
    new Date(iso).toLocaleString(undefined, { timeZone: tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  return (
    <div className="page">
      <h1>{c("nav.orders")}</h1>
      <div className="form-row">
        <label>
          {c("orders.vendor")}
          <select value={form.vendor_id} onChange={(e) => setForm({ ...form, vendor_id: e.target.value })}>
            {approved.map((v) => <option key={v.vendor_id} value={v.vendor_id}>{v.display_name}</option>)}
          </select>
        </label>
        <label>
          {c("orders.store")}
          <select value={form.store_id} onChange={(e) => setForm({ ...form, store_id: e.target.value })}>
            {(retailer.stores ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>
          {c("orders.date")}
          <input type="date" value={form.for_date} onChange={(e) => setForm({ ...form, for_date: e.target.value })} />
        </label>
        <button disabled={!form.vendor_id || !form.store_id} onClick={() => void place()}>{c("orders.place")}</button>
      </div>
      {error && <div className="error">{error}</div>}
      {!orders.length && <p className="empty">{c("orders.none")}</p>}
      {orders.map((o) => (
        <article key={o.order_id} className="card">
          <header>
            <code>{o.order_id}</code> · <strong>{vendorName(o.vendor_id)}</strong> · {storeName(o.store_id)} <Pill status={o.status} />
            {o.queue_reason && <span className="muted"> — {o.queue_reason}</span>}
            {o.mandate_terms_hash && <span className="muted"> · <Icon name="signed" size={15} /> {shortHash(o.mandate_terms_hash)}</span>}
          </header>
          <table className="versions">
            <tbody>
              {o.versions.map((v, i) => {
                const d = (o.decisions ?? []).find((x) => x.version === v.version);
                return (
                  <tr key={v.version}>
                    <td>v{v.version}</td>
                    <td>{v.lines.map((l) => `${l.qty} ${l.unit} ${l.sku} @ ${v.currency} ${l.price}`).join(", ")}</td>
                    <td>{when(v.pickup_at)}</td>
                    <td>{v.payment_days}d</td>
                    <td>
                      {d && <Pill status={d.status} />}
                      {(d?.reasons ?? []).map((r) => <span key={r.code} className="flag">{r.code}</span>)}
                      {!d && i > 0 && <span className="muted">{c("orders.counter")}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </article>
      ))}
    </div>
  );
}
