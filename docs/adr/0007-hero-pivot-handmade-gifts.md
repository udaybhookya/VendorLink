# ADR 0007 — Hero demo pivot: config A becomes handmade gifts

Status: accepted · 17 Oct 2026

## Problem
Config A (fresh produce, quick commerce) told the story with bunches of spinach and 05:30 pickups.
The hero demo moves to home makers selling handmade gifts (candles, home décor) to a corporate
gifting buyer, while config B (packaged food, Indonesia, Sara) stays as it is. It has to be done
the way the product claims it can be: configuration, not code. No changes to the Guardian,
`vl_core` schemas, the trust gateway, the Orchestrator or any core agent.

## Decisions
1. **New config, old config kept as spares.** `handmade_gifts` (business), `gifting` (retailer),
   `default_gifting` (policy) and the `gifting` workflow set; preset A points to them.
   `fresh_produce`, `quick_commerce`, `default_qc` and the Produce Grader stay in `config/`,
   unused by any preset. They cost nothing, keep their eval sets meaningful, and show a third
   configuration is a file away.
2. **Ordering is `order.create`.** The `gifting` workflow set routes `place_order` to
   `order.create` (the Replenishment Planner's one-off skill); config B keeps `order.replenish`
   through `default.json`. The order's date is its pickup date.
3. **Craft Quality Checker (`grade.craft`)**, a new specialist. The vision model only describes
   the photo (`CraftResult`: is it a product, grade, finish, defects from a list, batch
   consistency, claims printed on the product, confidence). Every flag is decided in code from
   config: `BELOW_MIN_GRADE` (retailer threshold), `DEFECT_*` for the Business Profile's
   `blocking_defects`, `BATCH_INCONSISTENT` when `require_batch_consistency`,
   `UNSUPPORTED_CLAIM_*` for the retailer's `claims_requiring_proof` found in her words or on the
   label (deduplicated against flags the Catalogue Builder already raised), `NOT_A_PRODUCT_PHOTO`,
   `NO_PHOTO`, and `CRAFT_NEEDS_REVIEW` when the model fails (never a default grade).
   `CraftResult` lives in the agent's module, not `vl_core.schemas`: it is the model's reply
   format and never crosses a service boundary; the skill's input and output are
   `ListingStepIn` / `ListingStepOut`.
4. **Hero numbers.** Maya: lavender candle ≥ ₹180, ≤ 40/day, pickup ≥ 10:00, paid ≤ 15 days,
   auto-accept ≤ ₹5,000; listed at ₹220. Corporate gifting desk (`store:hub-4`): 50 @ 08:00 on
   net-45 → red with exactly `QTY_OVER_DAILY_MAX`, `PICKUP_BEFORE_WINDOW`, `PAYMENT_TOO_LATE` →
   counter 40 @ 10:00, 15 days (₹8,800) → 20% value change > `auto_accept_counter_max_delta` 0.10
   → queue → the Liaison asks the buyer on Slack → approved → ₹8,800 > ₹5,000 so Maya must say yes
   → signed. `auto_order_max_value` ₹25,000 lets the order go out without a pre-approval.
5. **Payment days come from the first term template.** `plan_for_store` uses
   `term_templates[0]`, and `PlaceOrderRequest` has no per-order terms. The gifting profile lists
   corporate net-45 first, so every config A order starts outside Maya's 15 days. A payment-only
   counter changes the value by 0%, so policy accepts it and the Guardian then judges v2 on its
   own (green or amber). The e2e script shows a first-time green and amber by putting the net-15
   template first through the config API (`use_terms()` in `scripts/_demo.py`), not by code.
6. **Escalation goes to a category lead.** Buyer approvals unanswered for 4 hours go to the new
   `category_lead` contact instead of `ops`. `routing.json` is shared, so this applies to config B
   too.
7. **Retailer and store ids are unchanged.** `retailer:freshcart`, `store:hub-*` and the
   `FRESHCART_*` variables stay (they are in `.env`, Slack channels and many tests); only Hub 4's
   display name becomes "Corporate gifting desk · Hyderabad".
8. **The vendor app still opens in English.** Maya speaks Telugu: the demo switches with the 🌐
   picker and she consents in Telugu, as before.

## Known gaps
- **"Counters that change payment days go to the queue"** cannot be expressed:
  `review_counter` only measures the change in order value, and `RetailerPolicy` forbids unknown
  fields. The hero reaches the queue through its 20% value change. Doing it properly needs a
  `RetailerPolicy` field (for example `counter_terms_change_needs_human`) and a line in
  `review_counter`: a schema and core-agent change, out of scope here.
- **Units are read back raw** ("40 piece of lavender candle"; in Telugu the unit stays English).
  The fix is a per-language unit label in i18n used by `vl_core/readback.py`; it affects config B
  too, so it is a separate change.
- The Produce Grader eval stops at its skip and never grades; the Craft Quality Checker eval
  does run once its 10 photos are in `seed/photos/`.
