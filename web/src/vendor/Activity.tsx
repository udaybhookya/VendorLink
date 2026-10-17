import { useEffect, useState } from "react";
import type { ActivityItem, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";

export function Activity({ vendor, t, tick }: { vendor: Vendor; t: T; tick: number }) {
  const [items, setItems] = useState<ActivityItem[]>([]);
  useEffect(() => {
    api<ActivityItem[]>(VENDOR_URL, `/vendors/${vendor.id}/activity?language=${vendor.language}`).then(setItems).catch(() => setItems([]));
  }, [vendor.id, vendor.language, tick]);

  return (
    <section className="activity">
      <h2>{t("ui.who")}</h2>
      {!items.length && <p className="empty">{t("ui.no_activity")}</p>}
      <ul>
        {items.map((a, i) => (
          <li key={i} className={a.outcome}>
            <time>{new Date(a.ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>
            <span>{a.text}</span>
            <small className="muted">{a.actor}</small>
          </li>
        ))}
      </ul>
    </section>
  );
}
