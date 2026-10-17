# ADR 0009 — The catalogue, and a photo pipeline that is checked end to end

Status: accepted · 17 Oct 2026 · builds on ADR 0008

## Problem
Testing ADR 0008 with real photos showed three gaps. There was no catalogue: a published listing
existed only as a row, its photos only as blob URIs nobody could view. No photo was ever
improved unless an image model was configured, and when one was, its output kept the input's
shape (a 1280×468 banner came back 1024×362, still below the retailer's minimum). And the
retailer *assumed* a version she picked met its standards; nothing checked it.

## Decisions
1. **A catalogue in the retailer service** (`retailer_svc/catalogue.py`, read-only):
   - `GET /retailers/{r}/catalogue?status=&vendor_id=&sku=`: product cards (`CatalogueItem`).
   - `GET /retailers/{r}/catalogue/{listing}`: `CatalogueEntry`, with the card, copy, attributes
     and the source of each fact, flags, every photo (`CatalogueImage`: kind, URLs, the
     regeneration prompt, which one is in use), the listing's history from the audit log (both
     sides' records for that listing), and the orders that include the product.
   - `GET /retailers/{r}/catalogue/{listing}/images/{image}?size=full|thumb`: the bytes from the
     blob store (thumbnails made on the fly). Photos are never inlined in catalogue responses.
   - The console has a Catalogue page; Orders link each product line to it.
2. **Status flows back to her** through a new gateway skill, `listing.status` (retailer →
   vendor, `ListingStatusUpdate`, both parties named and checked against the credentials). The
   retailer sends it when a person decides in the review queue, so her copy no longer stays
   `needs_review` after the category manager approved it.
3. **Two kinds of improved photo.** `ImageCandidate.kind` is `original | enhanced | generated`:
   - `enhanced`: her photo cleaned up **in code** (`vl_core.imaging.auto_enhance`: exposure by
     gamma for dark photos, a contrast stretch that may not darken a photo that was not dark, a
     gentle sharpen, very wide or tall photos centre-cropped to 4:3, then padded to a square with
     the photo's own background colour; never upscaled). Not AI, labelled as such, always offered
     when the photo misses a standard (`ImageStandards.classic_enhancement`).
   - `generated`: AI-regenerated from her photo (ADR 0008), now rendered **in parallel** and in
     the retailer's catalogue shape (`ImageStandards.aspect_ratio`, default 1:1, passed to the
     image model as `image_config.aspect_ratio`).
4. **Her pick is checked, not assumed.** On `listing.confirm`, if she chose any version, the
   retailer drops the old photo's flags and runs the same `image.assess` agent on the chosen
   photo; the listing keeps whatever that check finds. `AI_PHOTO_NEEDS_REVIEW` still follows the
   retailer's policy.
5. **Relays wait for agents.** Building a listing makes about five model calls plus image renders
   (about 60 s with an image model). `VL_RELAY_TIMEOUT_SECONDS` (default 120) applies to relays
   only; other calls keep the 30 s default. A server error is answered inside the CORS layer so
   the browser shows it instead of "Failed to fetch".

## Verified
With real Gemini and `gemini-3.1-flash-image` on the cluttered demo photo: three 1024×1024 AI
versions and one auto-enhanced version; she picks Photo 1; the recheck clears every photo flag;
`AI_PHOTO_NEEDS_REVIEW` sends it to the category manager; the catalogue serves the photo.
Offline: imaging unit tests, the three eval sets, Guardian unit tests, a gateway test that a
retailer cannot report on another retailer's listing, and end-to-end tests for the catalogue,
the image endpoint, the status relay and the recheck.

## Known costs and gaps
- Drafting a listing with an image model takes about a minute; the vendor app says so while it
  waits. Moving the photo work to a background job (draft first, photos when ready) would need a
  vendor-side notification and is left for later.
- The vendor app shows small previews carried in her listing copy; the full images stay with the
  retailer.
