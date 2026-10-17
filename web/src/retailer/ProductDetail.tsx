import { useState } from "react";
import { Link } from "react-router-dom";
import type { CatalogueEntry, CatalogueImage } from "../types/generated";
import { api, RETAILER_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { Icon } from "../lib/icons";
import { Pill, useLoad } from "./Console";
import { explain } from "./codes";

export const img = (path?: string | null) => (path ? `${RETAILER_URL}${path}` : "");

/** One product as the retailer sees it: photos (hers, auto-enhanced, AI), copy, each fact with
 *  where it came from, flags in words, orders and history. Used by Catalogue and Inbox. */
export function ProductDetail({ retailerId, listingId, tick, onClose, compact = false }: {
  retailerId: string; listingId: string; tick: number; onClose?: () => void;
  /** Inside the Inbox: the decision bar already names the product. */
  compact?: boolean;
}) {
  const [entry, error] = useLoad<CatalogueEntry | null>(
    () => api<CatalogueEntry>(RETAILER_URL, `/retailers/${retailerId}/catalogue/${listingId}`), [listingId, tick], null);
  const [shown, setShown] = useState<string | null>(null);
  if (error) return <div className="error">{error}</div>;
  if (!entry) return null;
  const it = entry.item;
  const images = entry.images ?? [];
  const main = images.find((i) => i.id === shown) ?? images.find((i) => i.chosen) ?? images.find((i) => i.url);
  // an improved photo in use is judged against hers: does it still show the real product?
  const original = images.find((i) => i.kind === "original" && i.url);
  const inUse = images.find((i) => i.chosen && i.kind !== "original" && i.url);
  const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
  return (
    <section className="product-detail card">
      {compact && original && inUse && (
        <div className="photo-compare">
          <figure><img src={img(original.url)} alt={c("catalogue.original")} /><figcaption>{c("catalogue.original")}</figcaption></figure>
          <figure><img src={img(inUse.url)} alt={photoLabel(inUse)} /><figcaption>{photoLabel(inUse)} · {c("catalogue.in_use")}</figcaption></figure>
        </div>
      )}
      <header className="detail-head" hidden={compact}>
        <div>
          <h2>{it.title}</h2>
          <span className="muted">{it.vendor_name} · {it.category_name} · <code>{it.listing_id}</code></span>
        </div>
        {onClose && <button className="ghost" onClick={onClose} aria-label={c("catalogue.close")}><Icon name="x" /></button>}
      </header>
      <div className="detail-grid">
        <div className="gallery">
          {main?.url ? <img className="gallery-main" src={img(main.url)} alt={it.title} /> : <div className="no-photo big"><Icon name="camera" /></div>}
          <div className="gallery-strip">
            {images.map((i) => <Thumb key={i.id} image={i} active={main?.id === i.id} onPick={() => setShown(i.id)} />)}
          </div>
          {main?.kind === "generated" && main.prompt && (
            <details className="prompt"><summary>{c("catalogue.prompt")}</summary><p>{main.prompt}</p></details>
          )}
        </div>
        <div className="stack-tight">
          <dl className="facts-head">
            <div><dt>{c("catalogue.price")}</dt><dd>{it.currency} {it.price} / {it.unit}</dd></div>
            <div><dt>{c("catalogue.available")}</dt><dd>{it.qty} {it.unit}</dd></div>
            {it.grade && <div><dt>{c("catalogue.grade")}</dt><dd>{it.grade}</dd></div>}
            <div><dt>{c("catalogue.status")}</dt><dd><Pill status={it.status} />
              {!compact && it.status === "needs_review" && <> <Link className="small" to="/retailer/inbox">{c("orders.decide")} →</Link></>}</dd></div>
          </dl>
          {entry.description && <p>{entry.description}</p>}
          {(entry.bullets ?? []).length > 0 && <ul>{(entry.bullets ?? []).map((b) => <li key={b}>{b}</li>)}</ul>}
          <h3>{c("catalogue.facts")}</h3>
          <table>
            <tbody>
              {Object.entries(entry.attributes ?? {}).map(([k, v]) => {
                const src = (entry.sources ?? {})[k];
                return <tr key={k}><td>{k}</td><td>{String(v)}</td><td>{src && <span className={`src ${src}`}>{c(`catalogue.src_${src}`)}</span>}</td></tr>;
              })}
              {(["price", "qty"] as const).map((k) => (entry.sources ?? {})[k] && (
                <tr key={k}><td>{k}</td><td>{k === "price" ? `${it.currency} ${it.price}` : `${it.qty} ${it.unit}`}</td>
                  <td><span className={`src ${(entry.sources ?? {})[k]}`}>{c(`catalogue.src_${(entry.sources ?? {})[k]}`)}</span></td></tr>
              ))}
            </tbody>
          </table>
          {(entry.flag_details ?? []).length > 0 && (
            <>
              <h3>{c("catalogue.flags")}</h3>
              <ul className="flag-list">
                {(entry.flag_details ?? []).map((f) => (
                  <li key={f.code} title={f.code}><strong>{explain(f.code)}</strong>{f.detail && <span className="muted small"> {f.detail}</span>}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
      <div className="detail-grid">
        <div>
          <h3>{c("catalogue.orders")}</h3>
          {!(entry.orders ?? []).length && <p className="muted">{c("catalogue.no_orders")}</p>}
          {(entry.orders ?? []).length > 0 && (
            <table>
              <tbody>
                {(entry.orders ?? []).map((o) => (
                  <tr key={o.order_id}><td><code>{o.order_id}</code></td><td>{o.store_name}</td><td>{o.qty} {o.unit}</td><td>{when(o.pickup_at)}</td><td><Pill status={o.status} /></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div>
          <h3>{c("catalogue.history")}</h3>
          <ol className="timeline">
            {(entry.history ?? []).map((h, n) => (
              <li key={n}><span className="muted small">{when(h.ts)}</span> <code>{h.action}</code> <span className="muted small">{h.actor}</span></li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

/** "Warm wood · AI" for a styled AI version; otherwise what kind of photo it is. */
const photoLabel = (image: CatalogueImage) =>
  image.kind === "generated" && image.look_name ? `${image.look_name} · AI` : c(`catalogue.${image.kind}`);

function Thumb({ image, active, onPick }: { image: CatalogueImage; active: boolean; onPick: () => void }) {
  const label = photoLabel(image);
  return (
    <button className={`gallery-thumb ${active ? "on" : ""}`} onClick={onPick} disabled={!image.url} title={image.url ? label : c("catalogue.prompt_only")}>
      {image.thumb_url ? <img src={img(image.thumb_url)} alt={label} loading="lazy" /> : <div className="no-photo"><Icon name="camera" /></div>}
      <span className="small">{label}{image.chosen ? ` · ${c("catalogue.in_use")}` : ""}</span>
    </button>
  );
}
