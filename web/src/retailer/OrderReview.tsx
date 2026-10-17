import { Link } from "react-router-dom";
import type { OrderProposal, Retailer, RetailerOrder, RetailerVendor } from "../types/generated";
import { c } from "../lib/i18n";
import { explain } from "./codes";
import { Pill } from "./Console";

const value = (v: OrderProposal) => v.lines.reduce((sum, l) => sum + Number(l.qty) * Number(l.price), 0);

/** An order to decide on: what the buyer asked for, what is proposed now, what changed (and by how
 *  much in value), and why the supplier's Guardian countered, in words. */
export function OrderReview({ order, retailer, vendors, tz }: {
  order: RetailerOrder; retailer: Retailer; vendors: RetailerVendor[]; tz?: string;
}) {
  const first = order.versions[0];
  const now = order.versions.at(-1) ?? first;
  const reasons = (order.decisions ?? []).flatMap((d) => d.reasons ?? []);
  const vendor = vendors.find((v) => v.vendor_id === order.vendor_id)?.display_name ?? order.vendor_id;
  const store = retailer.stores?.find((s) => s.id === order.store_id)?.name ?? order.store_id;
  const when = (iso: string) =>
    new Date(iso).toLocaleString(undefined, { timeZone: tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const money = (n: number) => `${now.currency} ${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  const line = (v: OrderProposal) => v.lines.map((l) => `${l.qty} ${l.unit} ${l.sku}`).join(", ");
  const price = (v: OrderProposal) => v.lines.map((l) => `${v.currency} ${l.price}/${l.unit}`).join(", ");
  const delta = value(first) ? (value(now) - value(first)) / value(first) : 0;
  const countered = order.versions.length > 1;
  const rows: [string, string, string][] = [
    [c("inbox.qty"), line(first), line(now)],
    [c("inbox.price"), price(first), price(now)],
    [c("inbox.pickup"), when(first.pickup_at), when(now.pickup_at)],
    [c("inbox.payment"), c("inbox.days", { n: first.payment_days }), c("inbox.days", { n: now.payment_days })],
    [c("inbox.value"), money(value(first)), money(value(now))],
  ];

  return (
    <div className="stack-tight order-review">
      <p className="muted">{vendor} → {store} · <code>{order.order_id}</code> <Pill status={order.status} /></p>
      <table className="compare">
        <thead>
          <tr><th />{countered && <th>{c("inbox.asked")} (v{first.version})</th>}<th>{c("inbox.now")} (v{now.version})</th></tr>
        </thead>
        <tbody>
          {rows.map(([label, a, b]) => (
            <tr key={label} className={countered && a !== b ? "changed" : ""}>
              <th>{label}</th>
              {countered && <td>{a}</td>}
              <td>{b}{label === c("inbox.value") && countered && delta !== 0 && (
                <span className={`delta ${delta < 0 ? "down" : "up"}`}> {delta > 0 ? "+" : ""}{Math.round(delta * 100)}%</span>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {reasons.length > 0 && (
        <>
          <h3>{c("inbox.guardian")}</h3>
          <ul className="flag-list">{reasons.map((r) => <li key={r.code} title={r.code}><strong>{explain(r.code)}</strong></li>)}</ul>
        </>
      )}
      {order.status === "countered" && <p className="muted small">{c("inbox.counter_note")}</p>}
      <p className="small">
        {now.lines.map((l) => (
          <Link key={l.sku} to={`/retailer/catalogue?vendor=${encodeURIComponent(order.vendor_id)}&sku=${encodeURIComponent(l.sku)}`}>
            {c("catalogue.view_product")}: {l.sku}
          </Link>
        ))}
      </p>
    </div>
  );
}
