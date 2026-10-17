import { useEffect, useState } from "react";
import type { DealView, Listing, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { Questions } from "./Questions";
import { Terms } from "./Terms";
import { DealCard } from "./DealCard";

/** Every deal and offer, newest state first; the ones waiting on her also show on Chat (Needs you). */
export function Deals({ vendor, t, tick, onChange }: { vendor: Vendor; t: T; tick: number; onChange: () => void }) {
  const [deals, setDeals] = useState<DealView[]>([]);
  const [listings, setListings] = useState<Listing[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Listing[]>(VENDOR_URL, `/vendors/${vendor.id}/listings`).then(setListings).catch(() => setListings([]));
  }, [vendor.id, tick]);

  useEffect(() => {
    api<DealView[]>(VENDOR_URL, `/vendors/${vendor.id}/deals?language=${vendor.language}`)
      .then(setDeals)
      .catch((e) => setError(String(e)));
  }, [vendor.id, vendor.language, tick]);

  return (
    <section className="deals">
      <Questions vendor={vendor} t={t} tick={tick} onChange={onChange} />
      <Terms vendor={vendor} t={t} tick={tick} onChange={onChange} />
      {!deals.length && <p className="empty">{t("ui.no_deals")}</p>}
      {error && <div className="error">{error}</div>}
      {deals.map((view) => (
        <DealCard key={view.deal.order_id} view={view} vendor={vendor} t={t} listings={listings} onChange={onChange} />
      ))}
    </section>
  );
}
