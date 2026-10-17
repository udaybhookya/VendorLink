# ADR 0008 — Listing enrichment: catalogue standards, AI photos, approval on her behalf

Status: accepted · 17 Oct 2026

## Problem
Retailers have catalogue standards (photo size, sharpness, light, a plain background; a proper
title, description and attributes). Small makers have phone photos and a few spoken words, so
their listings were drafted but rarely catalogue-ready. Asked for: use AI to enrich the listing
(2–3 regenerated photos from her raw photo, plus product copy); her agent approves the result
conditionally and asks her only when it must; then the retailer approves.

## Decisions
1. **Standards are configuration.** `RetailerProfile.catalogue_standards` (`ImageStandards`,
   `TextStandards`). No standards, no checks (config B is unchanged). Required attributes come
   from the Business Profile's `attributes[].required`.
2. **Three retailer-side specialists**, loaded by the gifting workflow as optional steps after
   `catalogue.build` and `grade.*`:
   - **Catalogue Image Checker** (`image.assess`): size, sharpness and brightness are measured in
     code (`vl_core.imaging`, Pillow). Sharpness is edge strength relative to contrast, so a dim
     but sharp photo is not called blurry. A vision model only describes background, clutter,
     framing, light and noise. Every `IMAGE_*` flag is decided in code; an unreadable photo is
     `IMAGE_UNREADABLE`; a failed model is `IMAGE_NEEDS_REVIEW`, never a pass.
   - **Image Enhancer** (`image.enhance`): runs only when photo flags exist and the retailer
     allows AI photos. A vision model plans the edits (what the product is, what must not change,
     what to fix); **code wraps each edit in a fixed faithfulness guard** ("keep the product
     exactly as it is… change only…"). The image model (`IMAGE_MODEL`, behind
     `vl_core.llm.ImageGen`) renders each from her photo, never from scratch. With no image model
     the prompts stay as prompts and the app shows retake tips in her language. A failed plan
     falls back to edits from the flags (`IMAGE_EDITS_FROM_RULES`); a failed render is
     `IMAGE_GENERATION_FAILED`.
   - **Listing Enricher** (`listing.enrich`): a model writes title, description, bullets and
     attributes. Code checks them: a fact the model says came from her must show in her words
     (its quoted evidence, or the value for text; a bare number is not enough), else its source
     is `model` and it gets `FACT_NOT_FROM_VENDOR_<key>`; a claim from the retailer's proof list
     she never made is flagged; missing required attributes are flagged; title and bullet limits
     come from the standards. `Listing.sources` records `vendor | answer | photo | model`.
3. **Her Guardian approves on her behalf, deterministically** (`guardian.review_listing`, a new
   internal Guardian skill; still no model in `agents/core/guardian/`). Inputs: the listing, her
   words, her answers and `Vendor.listing_rules` (`price_must_match`, `allow_text_rewrite`,
   `allow_ai_images`). Reasons she must look: `PRICE_NOT_WHAT_SHE_SAID`,
   `QTY_NOT_WHAT_SHE_SAID`, `FACTS_NOT_FROM_HER`, `TEXT_REWRITE_NEEDS_HER_OK`,
   `AI_PHOTO_NEEDS_HER_OK`, `PHOTO_BELOW_STANDARD`. By default an AI photo always needs her: only
   she can say it still looks like her product.
4. **She is asked in the app only.** The Liaison asks a `review` question (choices: her photo,
   Photo 1…n, don't list it); routing in `config/hitl/routing.json` (in-app, then a Gmail nudge).
   A choice that arrives from any channel but the app is ignored, as with consent.
5. **The retailer checks the choice.** `listing.confirm` carries her chosen photo; the retailer
   accepts only photos its own agents offered (`photo_not_offered`), lets an enhanced photo
   answer the `IMAGE_*` flags, and adds `AI_PHOTO_NEEDS_REVIEW` when
   `ai_images_need_review` is on, so a category manager approves it (Slack) before publishing.
   The copy fields are part of the confirm hash, so they cannot change on the way.
6. **Previews travel, images stay.** `ImageCandidate.preview_b64` (a small JPEG) lets the vendor
   app show the versions; the full images stay in the retailer's blob store.

## Consequences
- 17 agents; 8 call a model. Every verdict is code and is covered offline: three eval sets
  (synthetic photos, scripted model replies), Guardian unit tests per reason code, and an
  end-to-end test from a cluttered photo to a published listing.
- A listing that is already right and fully hers is now approved by her Guardian without a tap
  (an audit record `listing.approved_by_guardian` and an activity line say so).
- Known gaps: the image model's faithfulness is guarded by the prompt and her eyes, not measured.
  (The listing status relay, the catalogue with its photo views and the recheck of her chosen
  photo were added in ADR 0009.)
