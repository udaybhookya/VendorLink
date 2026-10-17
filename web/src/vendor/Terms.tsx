import { useEffect, useState } from "react";
import type { Vendor, VendorTerms } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { Icon } from "../lib/icons";

/** Terms retailers propose, with her Guardian's verdict; she accepts or declines. */
export function Terms({ vendor, t, tick, onChange }: { vendor: Vendor; t: T; tick: number; onChange: () => void }) {
  const [items, setItems] = useState<VendorTerms[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api<VendorTerms[]>(VENDOR_URL, `/vendors/${vendor.id}/terms`).then(setItems).catch(() => setItems([]));
  }, [vendor.id, tick]);

  async function answer(item: VendorTerms, action: "accept" | "decline") {
    setError("");
    try {
      await api(VENDOR_URL, `/vendors/${vendor.id}/terms/${item.terms.terms_id}/${action}`, { method: "POST" });
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  if (!items.length) return null;
  return (
    <section className="stack-tight">
      <h3 className="section-title"><Icon name="terms" /> {t("ui.terms.title")}</h3>
      {error && <div className="error">{error}</div>}
      {items.map((item) => {
        const outside = item.verdict === "PAYMENT_TOO_LATE";
        return (
          <article key={item.terms.terms_id} className={`deal ${outside ? "red" : "green"}`}>
            <header>
              <strong>{item.terms.retailer_id.split(":")[1]}</strong>
              <span className="muted">{t("ui.terms.paid_within", { days: item.terms.payment_days })}</span>
            </header>
            {item.terms.description?.[vendor.language] || item.terms.description?.en ? (
              <p>{item.terms.description?.[vendor.language] ?? item.terms.description?.en}</p>
            ) : null}
            {outside && <p className="bad small"><Icon name="alert" size={15} /> {t("ui.terms.outside_rules")}</p>}
            {item.verdict === "NO_STANDING_MANDATE" && <p className="muted">{t("ui.terms.no_rules")}</p>}
            {item.status === "proposed" ? (
              <div className="actions">
                <button className="yes" disabled={outside} onClick={() => void answer(item, "accept")}><Icon name="check" /> {t("ui.terms.accept")}</button>
                <button className="no" onClick={() => void answer(item, "decline")}><Icon name="x" /> {t("ui.terms.decline")}</button>
              </div>
            ) : (
              <p className="sig">{item.status === "accepted" ? t("ui.terms.accepted") : t("ui.terms.declined")}</p>
            )}
          </article>
        );
      })}
    </section>
  );
}
