import { useState } from "react";
import type { ImageCandidate, Listing, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { speak } from "../lib/speech";
import { Icon } from "../lib/icons";

export type Photo = { data_b64: string; content_type: string };

export const jpeg = (b64: string) => `data:image/jpeg;base64,${b64}`;
export const photoSrc = (p: Photo) => `data:${p.content_type};base64,${p.data_b64}`;

/** What the retailer's agents made of her product, and her choice: in the chat and on Products.
 *  Her Guardian may already have approved it within her rules (`autoApproved`); otherwise she
 *  picks the photo and sends it, or decides not to list it. */
export function ListingResult({ vendor, t, listing, readback, autoApproved, reasons, ownPhoto, onDone }: {
  vendor: Vendor; t: T; listing: Listing | null; readback: string; autoApproved: boolean;
  reasons: string[]; ownPhoto: Photo | null; onDone: () => void;
}) {
  const [done, setDone] = useState<"sent" | "withdrawn" | null>(null);
  const [error, setError] = useState("");

  async function choose(image: string) {
    if (!listing) return;
    setError("");
    try {
      if (image === "reject") {
        await api(VENDOR_URL, `/vendors/${vendor.id}/listings/${listing.id}/withdraw`, { method: "POST" });
        setDone("withdrawn");
      } else {
        await api(VENDOR_URL, `/vendors/${vendor.id}/listings/${listing.id}/confirm`, { method: "POST", json: { image } });
        setDone("sent");
      }
      onDone();
    } catch (e) {
      setError(String(e));
    }
  }

  if (!listing) return <div className="listing-result"><p>{t("ui.products.needs_review")}</p></div>;
  return (
    <div className="listing-result stack-tight">
      {readback && (
        <p className="readback" onClick={() => speak(readback, vendor.language)}>
          <Icon name="speaker" /> <span>{readback}</span>
        </p>
      )}
      <ListingCopy listing={listing} />
      {error && <div className="error">{error}</div>}
      {autoApproved || done === "sent" ? (
        <p className="approved"><Icon name="signed" /> {autoApproved ? t("ui.products.approved") : t("ui.products.sent")}</p>
      ) : done === "withdrawn" ? (
        <p className="muted">{t("ui.flow.not_listed")}</p>
      ) : (
        <Review vendor={vendor} listing={listing} reasons={reasons} ownPhoto={ownPhoto} t={t} onChoose={(image) => void choose(image)} />
      )}
    </div>
  );
}

/** The title, description and bullet points the retailer's agents wrote from her facts. */
function ListingCopy({ listing }: { listing: Listing }) {
  if (!listing.title && !listing.description) return null;
  return (
    <div className="listing-copy">
      {listing.title && <strong>{listing.title}</strong>}
      {listing.description && <p>{listing.description}</p>}
      {(listing.bullets ?? []).length > 0 && <ul>{(listing.bullets ?? []).map((b) => <li key={b}>{b}</li>)}</ul>}
    </div>
  );
}

/** She chooses: her own photo or an AI-enhanced version; retake tips when no version could be
 *  made; or not to list it. Her agent asked because of `reasons` (shown in her language). */
function Review({ vendor, listing, reasons, ownPhoto, t, onChoose }: {
  vendor: Vendor; listing: Listing; reasons: string[]; ownPhoto: Photo | null; t: T; onChoose: (image: string) => void;
}) {
  const images = listing.images ?? [];
  const original = images.find((c) => c.kind === "original");
  const versions = images.filter((c) => c.kind !== "original" && c.uri && c.preview_b64);
  const photoFlags = (listing.flags ?? []).map((f) => f.code).filter((c) => c.startsWith("IMAGE_"));
  const ownSrc = original?.preview_b64 ? jpeg(original.preview_b64) : ownPhoto ? photoSrc(ownPhoto) : null;
  const tile = (c: ImageCandidate | undefined, src: string | null, caption: string, id: string) => (
    <figure key={id} className="photo-choice">
      {src ? <img src={src} alt={caption} /> : <div className="photo-missing"><Icon name="camera" /></div>}
      <figcaption>{caption}</figcaption>
      <button className={c?.kind === "generated" ? "yes" : "ghost"} onClick={() => onChoose(id)}>
        {id === "original" && !versions.length ? t("ui.products.send_as_is") : t("ui.products.use_photo")}
      </button>
    </figure>
  );
  return (
    <div className="stack-tight">
      <p className="check"><Icon name="alert" /> {t("ui.products.check")}</p>
      {reasons.length > 0 && <ul className="reasons">{reasons.map((r) => <li key={r}>{t(`listing_reason.${r}`)}</li>)}</ul>}
      <div className="photo-choices">
        {tile(original, ownSrc, t("ui.products.your_photo"), "original")}
        {versions.map((c) => tile(c, jpeg(c.preview_b64 as string),
          c.kind === "enhanced" ? t("ui.products.enhanced")
            : c.look_name?.[vendor.language] || c.look_name?.en ? t("ui.products.look_ai", { name: c.look_name?.[vendor.language] || c.look_name?.en || "" })
            : t("ui.products.photo_n", { n: c.id.replace("img-", "") }), c.id))}
      </div>
      {!versions.length && photoFlags.length > 0 && (
        <div className="tips">
          <strong>{t("ui.products.retake")}</strong>
          <ul>{photoFlags.map((c) => <li key={c}>{t(`photo_tip.${c}`)}</li>)}</ul>
        </div>
      )}
      <button className="ghost" onClick={() => onChoose("reject")}>{t("ui.products.dont_list")}</button>
    </div>
  );
}
