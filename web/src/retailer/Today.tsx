import type { QueueItem, RetailerOrder, SecurityEvent } from "../types/generated";
import { api, GATEWAY_URL, RETAILER_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Link } from "react-router-dom";
import { Icon } from "../lib/icons";
import { explain } from "./codes";

export function Today({ retailer, active, tick }: PageProps) {
  const [queue] = useLoad(() => api<QueueItem[]>(RETAILER_URL, `/retailers/${retailer.id}/queue`), [tick], []);
  const [orders] = useLoad(() => api<RetailerOrder[]>(RETAILER_URL, `/retailers/${retailer.id}/orders`), [tick], []);
  const [events] = useLoad(() => api<SecurityEvent[]>(GATEWAY_URL, "/security-events"), [tick], []);
  const pending = queue.filter((q) => q.status === "pending");
  const byStatus = orders.reduce<Record<string, number>>((acc, o) => ({ ...acc, [o.status]: (acc[o.status] ?? 0) + 1 }), {});

  return (
    <div className="page">
      <h1>{c("nav.today")}</h1>
      <div className="tiles">
        <Link to="/retailer/inbox" className={`tile tile-link ${pending.length ? "alert-soft" : ""}`}>
          <div className="tile-num">{pending.length}</div>
          <div>{c("today.pending")}</div>
        </Link>
        <div className="tile">
          <div className="tile-num">{orders.length}</div>
          <div>{c("today.orders")}</div>
          <div className="pills">
            {Object.entries(byStatus).map(([s, n]) => (
              <span key={s}>
                <Pill status={s} /> {n}
              </span>
            ))}
          </div>
        </div>
        <div className={`tile ${events.length ? "alert" : ""}`}>
          <div className="tile-num">{events.length}</div>
          <div>{c("today.events")}</div>
        </div>
        <div className="tile">
          <div className="small muted">{c("today.config")}</div>
          <strong>{active?.business_profile.id} · {active?.retailer_profile.id} · {active?.country_pack.code}</strong>
          <div className="chips">{active?.specialists.map((s) => <span key={s} className="chip">{s}</span>)}</div>
        </div>
      </div>
      {pending.length > 0 && (
        <>
          <h2>{c("today.pending")}</h2>
          <ul className="item-list">
            {pending.map((q) => (
              <li key={q.id}>
                <span className="pill">{c(`inbox.kind_${q.kind}`)}</span>
                <div>{inWords(q.reason)}</div>
                <Link to={`/retailer/inbox?item=${encodeURIComponent(q.id)}`} className="item-link">{c("nav.inbox")} <Icon name="send" size={14} /></Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** A queue reason with its machine codes said in words. */
const inWords = (reason: string) => {
  const codes = reason.match(/\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b/g);
  return codes ? codes.map(explain).join(" · ") : reason;
};
