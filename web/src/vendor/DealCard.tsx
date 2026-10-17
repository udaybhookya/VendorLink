import { useState } from "react";
import type { DealView, Listing, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { speak } from "../lib/speech";
import { Icon } from "../lib/icons";
import { jpeg } from "./ListingCard";

/** One deal: what was asked, her agent's read-back, the versions, and her yes/no when it waits on her. */
export function DealCard({ view, vendor, t, listings, onChange }: {
  view: DealView; vendor: Vendor; t: T; listings: Listing[]; onChange: () => void;
}) {
  const [error, setError] = useState("");
  const d = view.deal;
  const colour = d.decisions?.at(-1)?.status ?? "amber";

  /** Her listing for an order line (same product name), with the photo the retailer shows. */
  const product = (sku: string) => {
    const l = listings.find((x) => x.sku_name.toLowerCase() === sku.toLowerCase() && x.status !== "rejected");
    const photo = l?.images?.find((c) => c.uri && (l.photos ?? []).includes(c.uri) && c.preview_b64)
      ?? l?.images?.find((c) => c.kind === "original" && c.preview_b64);
    return l ? { title: l.title || l.sku_name, preview: photo?.preview_b64 ?? null } : null;
  };

  async function answer(yes: boolean) {
    setError("");
    try {
      if (yes) {
        // A tap on "Yes" after the read-back is her consent; the transcript records how it was given.
        const word = t.words("affirmatives")[0] ?? "yes";
        await api(VENDOR_URL, `/vendors/${vendor.id}/deals/${d.order_id}/consent`, {
          method: "POST",
          json: { subject_hash: view.subject_hash, transcript: `${word} (tap)`, language: vendor.language },
        });
      } else {
        await api(VENDOR_URL, `/vendors/${vendor.id}/deals/${d.order_id}/decline`, { method: "POST" });
      }
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <article className={`deal ${colour}`}>
      <header>
        <span className={`dot ${colour}`} aria-hidden />
        <strong>{t(`deal_status.${d.status}`)}</strong>
        <span className="muted">
          {t("ui.version")} {d.versions.at(-1)?.version}
        </span>
      </header>
      {(d.versions.at(-1)?.lines ?? []).map((line) => {
        const p = product(line.sku);
        return p && (
          <div key={line.sku} className="deal-product">
            {p.preview && <img src={jpeg(p.preview)} alt="" />}
            <span>{p.title}</span>
          </div>
        );
      })}
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
      {error && <div className="error">{error}</div>}
      {d.status === "awaiting_consent" && (
        <div className="actions">
          <button className="yes" onClick={() => void answer(true)}>
            <Icon name="check" /> {t("ui.yes_sign")}
          </button>
          <button className="no" onClick={() => void answer(false)}>
            <Icon name="x" /> {t("ui.no")}
          </button>
        </div>
      )}
      {d.vendor_sig && <p className="sig"><Icon name="signed" /> {t("ui.signed_badge")}</p>}
    </article>
  );
}
