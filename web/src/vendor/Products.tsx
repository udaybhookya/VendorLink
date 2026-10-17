import { useEffect, useState } from "react";
import type { Listing, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { Icon } from "../lib/icons";
import { jpeg, ListingResult } from "./ListingCard";

/** Her catalogue. Products are added in the chat (photo + her words); a listing still waiting on
 *  her can be finished here too. */
export function Products({ vendor, t, tick, onChange, onAdd }: {
  vendor: Vendor; t: T; tick: number; onChange: () => void; onAdd: () => void;
}) {
  const [listings, setListings] = useState<Listing[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api<Listing[]>(VENDOR_URL, `/vendors/${vendor.id}/listings`).then(setListings).catch(() => setListings([]));
  }, [vendor.id, tick]);

  return (
    <section className="stack products-page">
      <button className="big add-product" onClick={onAdd}>
        <Icon name="camera" /> {t("ui.products.add")}
      </button>
      <p className="muted small">{t("ui.products.add_hint")}</p>
      {!listings.length && <p className="empty">{t("ui.products.none")}</p>}
      <div className="listing-grid">
        {listings.map((l) => {
          const shown = (l.images ?? []).find((c) => c.uri && (l.photos ?? []).includes(c.uri) && c.preview_b64)
            ?? (l.images ?? []).find((c) => c.kind === "original" && c.preview_b64);
          return (
            <article key={l.id} className="deal listing-card">
              {shown?.preview_b64 && <img className="listing-thumb" src={jpeg(shown.preview_b64)} alt="" />}
              <div className="stack-tight">
                <header>
                  <strong>{l.title || l.sku_name}</strong>
                  <span className={`pill ${l.status}`}>{t(`listing_status.${l.status}`)}</span>
                </header>
                <p>{l.qty} {l.unit} · {l.currency} {l.price}{l.grade ? ` · ${l.grade}` : ""}
                  {l.ai_enhanced && <span className="pill ai">{t("ui.products.ai")}</span>}</p>
                {(l.flags ?? []).length > 0 && <div>{(l.flags ?? []).map((f) => <span key={f.code} className="flag">{f.code}</span>)}</div>}
                {l.status === "draft" && open !== l.id && (
                  <button className="secondary" onClick={() => setOpen(l.id)}>{t("ui.products.finish")}</button>
                )}
              </div>
              {open === l.id && (
                <div className="listing-finish">
                  <ListingResult vendor={vendor} t={t} listing={l} readback="" autoApproved={false} reasons={[]}
                    ownPhoto={null} onDone={() => { setOpen(null); onChange(); }} />
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
