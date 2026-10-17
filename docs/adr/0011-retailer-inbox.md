# ADR 0011 — One Inbox for retailer decisions, shown with their context

Status: accepted · 17 Oct 2026 · web only (`web/src/retailer/`) · companion to ADR 0010

## Problem
A decision a person had to make appeared in three places: Today, Tasks ("Waiting for people")
and the Review queue, all of them the same approval queue item and its Slack/email task. None
showed what was being decided: a listing review offered approve / reject next to raw codes
(`ATTRIBUTE_MISSING_SIZE, AI_PHOTO_NEEDS_REVIEW`) and a listing id, with the product, its photos
and its copy one page and a search away. A counter showed no comparison with what was asked.
Ten flat nav items mixed daily work with admin pages.

## Decisions
1. **Inbox** (`/retailer/inbox`) replaces Tasks and Review queue (their routes redirect). It
   lists every pending queue item (with the task that asks about it on Slack/email) and every
   open task not tied to one (e.g. an order a replenishment run holds). Rows name the product,
   order or supplier, not ids. The nav shows a count.
2. **Decide with the context in view.** The selected item has the decide buttons at the top
   (worded per kind: *Approve and publish*, *Accept the counter* …) and below them:
   - **why** it needs a person, each code in words (`codes.ts` + `console.en.json` `codes`;
     prefix rules for `ATTRIBUTE_MISSING_*`, `MISSING_REGISTRATION_*`; the code stays a tooltip),
     and that deciding here closes the Slack/email request (it does: `resolve` closes the task);
   - a **listing**: the catalogue entry (`ProductDetail`, shared with Catalogue): her photo
     beside the improved photo in use, all versions, copy, each fact with its source, flags,
     history;
   - an **order or counter** (`OrderReview`): asked vs now per field, changed rows highlighted,
     the value change in %, the Guardian's reasons in words;
   - a **supplier**: the document checks;
   - a **run's held order**: the facts the run asked with (value against the limit).
   After a decision the next item opens. **Done** lists what was decided and suppliers' answers.
3. **Nav**: Today · Inbox · Orders · Catalogue · Suppliers, then *Admin*: Configuration,
   Observability, Trust & audit, Outbox (dev). Replenishment runs moved from Tasks to Orders.
   Today's "Waiting for you" opens the item in the Inbox; Orders and Catalogue link to it.

## Not changed
No API or schema change: the Inbox uses the existing queue, task, listing, order, vendor and
catalogue routes, and decides through `queue/{id}/{approve|reject}` or `tasks/{id}/answer` as
before. A run's held order has no saved `RetailerOrder` yet, so it shows the task's parameters,
not order lines; saving the proposal with the job would let it use `OrderReview` too.
