import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { CatalogueItem } from "../types/generated";
import { api, RETAILER_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { type PageProps, Pill, useLoad } from "./Console";
import { Icon } from "../lib/icons";
import { img, ProductDetail } from "./ProductDetail";

const FILTERS = ["all", "published", "needs_review", "rejected"] as const;

/** The retailer's catalogue: every supplier product with its photos (her original, auto-enhanced
 *  and AI versions), the copy, where each fact came from, its history and its orders.
 *  `?listing=<id>` opens one; `?vendor=<id>&sku=<name>` opens the product an order refers to. */
export function Catalogue({ retailer, tick }: PageProps) {
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const query = filter === "all" ? "" : `?status=${filter}`;
  const [items] = useLoad(() => api<CatalogueItem[]>(RETAILER_URL, `/retailers/${retailer.id}/catalogue${query}`), [tick, query], []);
  const open = params.get("listing");

  // An order links here by supplier and product name: resolve it to the listing.
  useEffect(() => {
    const vendor = params.get("vendor");
    const sku = params.get("sku");
    if (!vendor || !sku || open) return;
    api<CatalogueItem[]>(RETAILER_URL, `/retailers/${retailer.id}/catalogue?vendor_id=${encodeURIComponent(vendor)}&sku=${encodeURIComponent(sku)}`)
      .then((found) => { if (found[0]) setParams({ listing: found[0].listing_id }); })
      .catch(() => undefined);
  }, [params, open, retailer.id, setParams]);

  return (
    <div className="page">
      <h1>{c("catalogue.title")}</h1>
      <p className="muted">{c("catalogue.subtitle")}</p>
      <div className="chips">
        {FILTERS.map((f) => (
          <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>{c(`catalogue.${f}`)}</button>
        ))}
      </div>
      {!items.length && <p className="empty">{c("catalogue.empty")}</p>}
      <div className="catalogue-grid">
        {items.map((it) => (
          <button key={it.listing_id} className={`product-card ${open === it.listing_id ? "on" : ""}`} onClick={() => setParams({ listing: it.listing_id })}>
            {it.thumb_url ? <img src={img(it.thumb_url)} alt="" loading="lazy" /> : <div className="no-photo"><Icon name="camera" /></div>}
            <div className="product-meta">
              <strong>{it.title}</strong>
              <span className="muted small">{it.vendor_name} · {it.category_name}</span>
              <span>{it.currency} {it.price} / {it.unit}{it.grade ? ` · ${it.grade}` : ""}</span>
              <span><Pill status={it.status} />{it.ai_enhanced && <span className="pill ai">{c("catalogue.ai")}</span>}</span>
            </div>
          </button>
        ))}
      </div>
      {open && <ProductDetail retailerId={retailer.id} listingId={open} tick={tick} onClose={() => setParams({})} />}
    </div>
  );
}
