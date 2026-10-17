# ADR 0010 — A chat-first vendor app

Status: accepted · 17 Oct 2026 · web only (`web/src/vendor/`)

## Problem
The vendor app had three columns on a laptop: four pages on the left, a page in the middle and
an assistant docked on the right. Things waiting on her (a deal to say yes to, a question, terms)
were spread over the Deals page. Adding a product was a form on Products, separate from the
assistant, even though the product is "talk to your assistant". A vendor who opened the app
could not tell where to start.

## Decisions
1. **Chat is home.** Navigation: **Chat · Deals · Products · My shop · Activity** (side nav on a
   laptop, tab bar on a phone). The chat stays mounted across tabs, so the conversation is
   still there when she comes back. Nothing else docks beside the other pages.
2. **Needs you.** Beside the chat (laptop) or as a collapsible strip above it (phone): open
   questions, offered terms, deals waiting for her yes and listings waiting for her photo
   choice, each answerable in place. The count is a badge on Chat. A consent question and its
   deal, or a listing review and its draft, count and show once (the card, with photos).
3. **Products are added in the chat.** "Add a product" (a chip, or the button on Products)
   asks for a photo; she sends it with the camera button, then says or types what it is, the
   price and how many a day (plus the label text for packaged food). The UI then calls
   `POST /vendors/{id}/listings/draft`, the same route the old form used, and the result comes
   back into the chat as a card: read-back, the copy the retailer's agents wrote, and the photo
   choice, or her Guardian's approval. This flow does not depend on a model: the Concierge's
   offline intents have no listing intent, so the UI runs it step by step. When a model *is*
   talking with her and creates a listing by itself (`draft_listing` tool → `listing` event),
   the same card is shown.
4. **Products is the catalogue** (status in her words: waiting for you, being checked, live, not
   listed); a draft can be finished there too. **My shop** (was Setup) shows progress as three
   steps (documents, rules signed, retailer); the rules form is behind "Change my rules" once
   she has signed rules.
5. Quick chips under the chat ("My deals", "My rules", "What did you do today?") send words
   the offline intents understand in each language (keep them so when changing either).

## Not changed
No API, schema, gateway or agent change. Consent is still a tap on "Yes" after the read-back
(deals, rules) or her own words to the Concierge; nothing in the chat flow signs anything.
