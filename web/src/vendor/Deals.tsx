import { useEffect, useState } from "react";
import type { DealView, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { speak } from "../lib/speech";
import { Questions } from "./Questions";
import { Terms } from "./Terms";
import { Icon } from "../lib/icons";


export function Deals({ vendor, t, tick, onChange }: { vendor: Vendor; t: T; tick: number; onChange: () => void }) {
  const [deals, setDeals] = useState<DealView[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api<DealView[]>(VENDOR_URL, `/vendors/${vendor.id}/deals?language=${vendor.language}`)
      .then(setDeals)
      .catch((e) => setError(String(e)));
  }, [vendor.id, vendor.language, tick]);

  async function answer(view: DealView, yes: boolean) {
    setError("");
    const id = view.deal.order_id;
    try {
      if (yes) {
        // A tap on "Yes" after the read-back is her consent; the transcript records how it was given.
        const word = t.words("affirmatives")[0] ?? "yes";
        await api(VENDOR_URL, `/vendors/${vendor.id}/deals/${id}/consent`, {
          method: "POST",
          json: { subject_hash: view.subject_hash, transcript: `${word} (tap)`, language: vendor.language },
        });
      } else {
        await api(VENDOR_URL, `/vendors/${vendor.id}/deals/${id}/decline`, { method: "POST" });
      }
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <section className="deals">
      <Questions vendor={vendor} t={t} tick={tick} onChange={onChange} />
      <Terms vendor={vendor} t={t} tick={tick} onChange={onChange} />
      {!deals.length && <p className="empty">{t("ui.no_deals")}</p>}
      {error && <div className="error">{error}</div>}
      {deals.map((view) => {
        const d = view.deal;
        const colour = d.decisions?.at(-1)?.status ?? "amber";
        return (
          <article key={d.order_id} className={`deal ${colour}`}>
            <header>
              <span className={`dot ${colour}`} aria-hidden />
              <strong>{t(`deal_status.${d.status}`)}</strong>
              <span className="muted">
                {t("ui.version")} {d.versions.at(-1)?.version}
              </span>
            </header>
            <p className="readback" onClick={() => speak(view.readback, vendor.language)}>
              <Icon name="speaker" /> <span>{view.readback}</span>
            </p>
            {d.versions.length > 1 && (
              <ol className="versions">
                {d.versions.map((v) => (
                  <li key={v.version}>
                    v{v.version}: {v.lines.map((l) => `${l.qty} ${l.unit} ${l.sku}`).join(", ")} ·{" "}
                    {new Date(v.pickup_at).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })}
                  </li>
                ))}
              </ol>
            )}
            {d.status === "awaiting_consent" && (
              <div className="actions">
                <button className="yes" onClick={() => void answer(view, true)}>
                  <Icon name="check" /> {t("ui.yes_sign")}
                </button>
                <button className="no" onClick={() => void answer(view, false)}>
                  <Icon name="x" /> {t("ui.no")}
                </button>
              </div>
            )}
            {d.vendor_sig && <p className="sig"><Icon name="signed" /> {t("ui.signed_badge")}</p>}
          </article>
        );
      })}
    </section>
  );
}
