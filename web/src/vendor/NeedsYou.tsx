import { useEffect, useState } from "react";
import type { DealView, HumanTask, Listing, Vendor, VendorTerms } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { Icon } from "../lib/icons";
import { DealCard } from "./DealCard";
import { Questions } from "./Questions";
import { Terms } from "./Terms";
import { ListingResult } from "./ListingCard";

export type Inbox = {
  /** Deals waiting for her yes. */
  deals: DealView[];
  listings: Listing[];
  /** Listings the retailer's agents made that she has not sent yet. */
  drafts: Listing[];
  count: number;
};

const EMPTY: Inbox = { deals: [], listings: [], drafts: [], count: 0 };

/** Everything waiting on her, in one place: the badge on Chat and the Needs-you panel. */
export function useInbox(vendor: Vendor | undefined, tick: number): Inbox {
  const [inbox, setInbox] = useState<Inbox>(EMPTY);
  useEffect(() => {
    if (!vendor) return;
    const id = vendor.id;
    const safe = <X,>(p: Promise<X[]>) => p.catch(() => [] as X[]);
    void Promise.all([
      safe(api<DealView[]>(VENDOR_URL, `/vendors/${id}/deals?language=${vendor.language}`)),
      safe(api<HumanTask[]>(VENDOR_URL, `/vendors/${id}/questions`)),
      safe(api<VendorTerms[]>(VENDOR_URL, `/vendors/${id}/terms`)),
      safe(api<Listing[]>(VENDOR_URL, `/vendors/${id}/listings`)),
    ]).then(([deals, questions, terms, listings]) => {
      const waiting = deals.filter((v) => v.deal.status === "awaiting_consent");
      const drafts = listings.filter((l) => l.status === "draft");
      // a consent question is its waiting deal, a listing review is its draft: count each once
      const open = questions.filter((q) => q.kind !== "notice" && q.kind !== "consent" && q.request.field !== "listing_review");
      const offers = terms.filter((x) => x.status === "proposed");
      setInbox({ deals: waiting, listings, drafts, count: waiting.length + open.length + offers.length + drafts.length });
    });
  }, [vendor?.id, vendor?.language, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  return inbox;
}

/** The to-do list beside the chat (laptop) or above it (phone): answer, accept, say yes. */
export function NeedsYou({ vendor, t, tick, inbox, onChange }: {
  vendor: Vendor; t: T; tick: number; inbox: Inbox; onChange: () => void;
}) {
  return (
    <div className="needs-you stack-tight">
      {inbox.count === 0 && <p className="all-clear"><Icon name="check" /> {t("ui.needs.all_clear")}</p>}
      <Questions vendor={vendor} t={t} tick={tick} onChange={onChange} skipConsent />
      <Terms vendor={vendor} t={t} tick={tick} onChange={onChange} pendingOnly />
      {inbox.deals.map((view) => (
        <DealCard key={view.deal.order_id} view={view} vendor={vendor} t={t} listings={inbox.listings} onChange={onChange} />
      ))}
      {inbox.drafts.map((l) => (
        <article key={l.id} className="deal amber">
          <header><strong>{l.title || l.sku_name}</strong><span className="pill draft">{t("listing_status.draft")}</span></header>
          <ListingResult vendor={vendor} t={t} listing={l} readback="" autoApproved={false} reasons={[]} ownPhoto={null} onDone={onChange} />
        </article>
      ))}
    </div>
  );
}
