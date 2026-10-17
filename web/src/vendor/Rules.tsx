import { useEffect, useState } from "react";
import type { RulesView, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { speak } from "../lib/speech";
import { Icon } from "../lib/icons";

export function Rules({ vendor, t, tick }: { vendor: Vendor; t: T; tick: number }) {
  const [view, setView] = useState<RulesView | null>(null);
  useEffect(() => {
    api<RulesView>(VENDOR_URL, `/vendors/${vendor.id}/rules`).then(setView).catch(() => setView(null));
  }, [vendor.id, tick]);

  const m = view?.mandate;
  if (!m) return <p className="empty">{t("ui.no_rules")}</p>;
  return (
    <section className="rules">
      <p className="readback" onClick={() => speak(view.readback ?? "", vendor.language)}>
        <Icon name="speaker" /> <span>{view.readback}</span>
      </p>
      <div className="rule-grid">
        {m.skus.map((s) => (
          <div key={s.sku} className="rule-card">
            <div className="rule-title">{s.sku}</div>
            <div><Icon name="floor" size={16} /> {t("ui.min_price")}: <b>{m.currency} {s.price_floor}</b> / {s.unit}</div>
            <div><Icon name="products" size={16} /> {t("ui.per_day")}: <b>{s.max_qty_per_day}</b> {s.unit}</div>
          </div>
        ))}
        <div className="rule-card">
          <div><Icon name="clock" size={16} /> {t("ui.pickup")}: <b>{m.pickup_windows.map((w) => `${w.start.slice(0, 5)}–${w.end.slice(0, 5)}`).join(", ")}</b></div>
          <div><Icon name="wallet" size={16} /> {t("ui.payment")}: <b>{m.payment_days_max}</b> {t("ui.days")}</div>
          <div><Icon name="store" size={16} /> {t("ui.buyers")}: <b>{(m.approved_buyers ?? []).join(", ")}</b></div>
          <div><Icon name="check" size={16} /> {t("ui.auto_accept")}: <b>{m.currency} {m.auto_accept.max_value}</b></div>
        </div>
      </div>
      <p className={`sig ${m.signature ? "" : "unsigned"}`}>
        {m.signature ? <><Icon name="signed" /> {t("ui.signed_badge")} · v{m.version}</> : t("ui.not_signed")}
      </p>
    </section>
  );
}
