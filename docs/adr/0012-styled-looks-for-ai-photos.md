# ADR 0012 — Each AI photo version in its own look

Status: accepted · 17 Oct 2026 · builds on ADR 0008 / 0009

## Problem
The AI versions of her photo all looked the same. Every version was wrapped in the retailer's
one `style` ("plain off-white background, soft daylight"), and the planning model was told to
vary only background, lighting, framing or sharpness within that style, so it returned near-
identical edits. She was choosing between copies.

## Decisions
1. **Looks are config.** `ImageStandards.looks: list[ImageLook]` (`id`, `name` by language,
   `scene`: backdrop, surface, light, angle, mood). Version *n* uses look *n* (repeating if the
   retailer wants more versions than it has looks; without looks, every version uses `style`,
   as before). Gifting has four: *Clean studio*, *Warm wood*, *Soft pastel*, *Linen*; with
   `candidates: 2` she sees the first two.
2. **The plan changed shape.** The vision model returns `product`, `keep`, `fixes` (corrections
   every version needs: clutter, framing, sharpness) and `scenes` (each look adapted to this
   product, in order). Code builds each prompt: faithfulness guard + fixes + the look's scene +
   the adapted scene. If the model fails, the looks still hold (fixes come from the flags,
   `IMAGE_EDITS_FROM_RULES`), so the versions stay different.
3. **Looks stay inside the retailer's standards**, because the retailer re-checks the photo she
   picks: bright, evenly lit, no props. (A dark "evening" look was dropped: it fails
   `min_brightness`.) The Catalogue Image Checker's "plain background" now includes a single
   surface against a plain or softly blurred backdrop, and gifting's `background`/`style` say so.
4. **Named for her.** `ImageCandidate.look` / `look_name` (and `CatalogueImage.look_name`):
   the apps caption a version "Warm wood · AI", and the question her agent asks offers the look
   names as choices she can say back.

## Checked
Eval set: 13/13 (new case: a plan without scenes still yields distinct looks; every case checks
that versions have distinct looks and the guard). Real models on the cluttered candle photo:
four clearly different versions (studio, wood, pastel, linen), product unchanged.
