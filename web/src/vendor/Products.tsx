import { useEffect, useState } from "react";
import type { Listing, ListingDraftView, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { downscale } from "../lib/image";
import { speak } from "../lib/speech";
import { Icon } from "../lib/icons";

/** Add a product (photo + words, optional label text) → the retailer's agents build the draft →
 *  read back → the supplier confirms. */
export function Products({ vendor, t, tick, onChange }: { vendor: Vendor; t: T; tick: number; onChange: () => void }) {
  const [listings, setListings] = useState<Listing[]>([]);
  const [text, setText] = useState("");
  const [label, setLabel] = useState("");
  const [photo, setPhoto] = useState<{ data_b64: string; content_type: string } | null>(null);
  const [draft, setDraft] = useState<ListingDraftView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const packaged = vendor.business_profile === "packaged_food";

  useEffect(() => {
    api<Listing[]>(VENDOR_URL, `/vendors/${vendor.id}/listings`).then(setListings).catch(() => setListings([]));
  }, [vendor.id, tick]);

  async function make() {
    setBusy(true);
    setError("");
    try {
      const view = await api<ListingDraftView>(VENDOR_URL, `/vendors/${vendor.id}/listings/draft`, {
        method: "POST",
        json: { vendor_id: vendor.id, language: vendor.language, transcript: text, label_text: label || null,
                photo_b64: photo?.data_b64 ?? null, photo_mime: photo?.content_type ?? "image/jpeg" },
      });
      setDraft(view);
      if (view.readback) speak(view.readback, vendor.language);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(id: string) {
    setError("");
    try {
      await api(VENDOR_URL, `/vendors/${vendor.id}/listings/${id}/confirm`, { method: "POST" });
      setDraft(null);
      setText("");
      setLabel("");
      setPhoto(null);
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  const listing = draft?.result.listing;
  return (
    <section className="stack products-page">
      <div className="stack">
      {error && <div className="error">{error}</div>}
      <div className="card-plain stack-tight">
        <h3>{t("ui.products.add")}</h3>
        <label className="camera-wide">
          <Icon name={photo ? "check" : "camera"} size={20} /> {photo ? t("ui.photo_added") : t("ui.photo")}
          <input type="file" accept="image/*" capture="environment" onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f) setPhoto(await downscale(f));
          }} />
        </label>
        <textarea rows={2} lang={vendor.language} placeholder={t("ui.products.describe")} value={text} onChange={(e) => setText(e.target.value)} />
        {packaged && <textarea rows={3} placeholder={t("ui.products.label_text")} value={label} onChange={(e) => setLabel(e.target.value)} />}
        <button disabled={busy || (!text && !photo)} onClick={() => void make()}>{t("ui.products.draft")}</button>
      </div>

      {draft && (
        <div className="readback-box">
          {listing ? <p>{draft.readback}</p> : <p>{t("ui.products.needs_review")}</p>}
          {(draft.result.flags ?? []).length > 0 && (
            <p className="muted">{t("ui.products.flags")}: {(draft.result.flags ?? []).map((f) => <span key={f.code} className="flag">{f.code}</span>)}</p>
          )}
          {listing && <button className="yes big" onClick={() => void confirm(listing.id)}><Icon name="check" /> {t("ui.products.confirm")}</button>}
        </div>
      )}

      </div>
      <div className="stack">
      {!listings.length && <p className="empty">{t("ui.products.none")}</p>}
      {listings.map((l) => (
        <article key={l.id} className="deal">
          <header>
            <strong>{l.sku_name}</strong>
            <span className={`pill ${l.status}`}>{l.status}</span>
          </header>
          <p>{l.qty} {l.unit} · {l.currency} {l.price}{l.grade ? ` · ${l.grade}` : ""}</p>
          {(l.flags ?? []).map((f) => <span key={f.code} className="flag">{f.code}</span>)}
        </article>
      ))}
      </div>
    </section>
  );
}
