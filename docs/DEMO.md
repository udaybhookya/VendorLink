# VendorLink demo runbook

About 8 minutes on a laptop, two app windows side by side: **Maya's VendorLink app** on the left
(`/vendor`) and the **FreshCart console** on the right (`/retailer`). All vendors, retailers and
registries are synthetic; the UI says so, and so should we.

**Recording setup:** install both as apps (Chrome → address bar → **Install** icon, or ⋮ → Cast,
save and share → **Install page as app**): "VendorLink" from `/vendor` and "VendorLink Console"
from `/retailer`. They open in their own windows without browser bars. On a wide window the vendor
app shows navigation, the page and the **Assistant** side by side; drag it narrower than about
1000 px and it becomes the phone layout with tabs. That's useful for a "same app on her phone"
moment.

## Before the demo

First-time setup and keys: `docs/LOCAL_SETUP.md`.

```bash
make dev                                          # 4 services + web; wait for "VendorLink ready"
uv run python scripts/demo_reset.py               # clean config A
uv run python scripts/e2e_order_loop.py           # smoke test: must print ORDER LOOP PASSED
uv run python scripts/demo_reset.py               # reset again after the smoke test
```

**Hosted demo** (docs/DEPLOY.md): `source infra/cloud_env.sh` first, then the same
`demo_reset.py` commands reset and seed the cloud services. Slack messages go to the real
workspace, so the buyer answers on Slack. The hosted console has no Outbox page.

`DEMO_SCENE=waiting make dev` starts straight into a scene. Start any scene only after the
"VendorLink ready" line; `make dev` resets once on startup and would wipe an earlier one.

- Without `GOOGLE_API_KEY` / model names the Concierge runs in **offline intent mode** (typed
  Telugu or English works; Live voice does not). Say so if asked; everything that decides or
  signs is deterministic either way.
- With `VL_CHANNELS=local` (the default), Slack and Gmail messages land in **Console → Outbox
  (dev)**, where you can reply as the person. With a real workspace (`VL_CHANNELS=mcp`), show
  the Slack message on a phone instead.
- Open the tabs in advance: console **Inbox**, **Orders**, **Suppliers**, **Observability**,
  **Trust**; Maya's **Deals** tab.

## Scene 1: the order loop (Config A, about 3 min)

*"Maya pours candles by hand at home in Hyderabad. She set her rules once, by voice: at least ₹180
a candle, at most 40 a day, pickup not before 10 am, paid within 15 days."*

1. Maya → **My shop**: show her rules, the standing mandate signed by her Guardian with her
   consent.
2. Console → **Orders** → vendor Maya, store **Corporate gifting desk**, tomorrow → **Place**.
   The corporate gifting buyer wants **50 candles at ₹220, pickup 08:00, paid in 45 days**.
3. The Guardian returns **red**: `QTY_OVER_DAILY_MAX`, `PICKUP_BEFORE_WINDOW`, `PAYMENT_TOO_LATE`.
   It counters with **40 @ 10:00, paid in 15 days** (order v2; v1 is never changed). *"No model
   decided this. The Guardian is plain code."*
4. The counter changes the order's value by 20%; FreshCart's gifting policy auto-accepts at most
   10%, so it goes to the console **Inbox** (asked v1 against countered v2, the value change and
   the Guardian's reasons in words) and the Human Liaison asks the buyer on Slack. Either click
   **Accept the counter** in the Inbox, or in **Outbox (dev)** (or Slack on a phone) reply
   **approve** as the buyer. Within a Liaison tick
   (or `curl -X POST localhost:8002/dev/tick`) the counter is accepted.
5. Maya → **Deals**: the deal is **amber**, waiting for her. Switch her app to **తెలుగు** with the
   🌐 picker in the header: the read-back, buttons and chat are now in Telugu. *"Her language,
   her choice."* She says **"అవును"** (yes): type it in **Chat**, or tap Yes on the deal.
6. The deal shows **signed**: an Order Mandate whose consent hash matches the exact proposal.
7. Console → **Trust** → **Verify** the audit chain ✓, then **Tamper** a record → verify shows
   the broken seq. *"Every state change is one audit record, hash-chained."*

## Scene 2: agents that ask people (Config A, about 2 min)

```bash
uv run python scripts/demo_reset.py --scene waiting
```

*"Most of this runs as a batch at night. Nobody watches a screen."*

1. Console → **Orders** → Replenishment runs: tomorrow's run is **waiting**; the Inbox shows the held order. Hub 7 and Hub 12 already went out
   (within policy); the corporate gifting desk's 50 × ₹220 is over the auto-order limit (the
   scene saved ₹10 000 in Studio) and waits for the buyer.
2. Console → **Outbox (dev)**: the buyer's Slack message ("over the auto-order limit…"). Reply
   **approve** as the buyer. Within a Liaison tick (or `curl -X POST localhost:8002/dev/tick`)
   the job resumes from where it paused (LangGraph checkpoint) and sends the order.
3. Maya → **Deals** → **Questions**: the planner asked, in her language, how many rose candles
   she can supply. She answers **20**; FreshCart sees it under the supplier.
4. Maya → **Deals** → **Terms**: FreshCart's corporate gifting terms (45 days; the scene proposed
   them, live it is **Suppliers → Terms**). Her Guardian checked them against her rules: she
   allows 15 days, so she cannot accept them. *"Her rules hold even for terms."* (After Studio
   puts the 15-day template first, the same step proposes terms she can accept.)
5. *"Slack and email may nudge Maya back to the app. They can never carry her consent."*

## Scene 2b: a listing that is not catalogue-ready (Config A, about 1.5 min)

*"Retailers have catalogue standards. Small makers have phone photos."*

1. Maya → **Chat** → **Add a product**: she sends a phone photo of a candle on a cluttered table
   (`seed/photos/demo/p2_cluttered.jpeg`; what each demo photo shows is in that folder's
   README), then says (or types) "40 lavender candles at 220 rupees, 250 grams". The listing
   comes back into the chat as a card.
2. The retailer's agents flag the photo (`IMAGE_CLUTTERED_BACKGROUND`, measured and described),
   prepare **an auto-enhanced version (code, not AI) and two AI-regenerated versions of her own
   photo** (the AI ones with `IMAGE_MODEL` set; otherwise retake tips in her language). Allow
   about a minute. and write the title, description and bullet points from her
   words. *"Every fact says where it came from. Nothing she did not say gets approved for her."*
3. Her Guardian approves listings within her rules, but not an AI photo: *"Please check… which
   photo should we use?"* She picks **Photo 2**.
4. FreshCart's policy sends AI photos to a person: the category manager gets it on **Slack**
   (Outbox in local mode), replies **approve**, and the listing is **published** with the AI photo
   disclosed. Or in console → **Inbox**: her photo beside the AI photo, the copy, each fact's
   source and the flags in words, then **Approve and publish**.
5. Show the same with a clean photo (`p1_clean_large.jpeg`; the small `p1_clean.jpeg` is under
   the 600×600 minimum and gets asked about): her Guardian approves it on her behalf, no
   question asked.
6. Console → **Catalogue**: the product with its photos (her original, the auto-enhanced and AI
   versions, which one is in use), the copy, each fact's source, its history and its orders.
   From **Orders**, *View product* opens the product an order is for.

## Scene 3: same agents, different market (Config B, about 1.5 min)

Console → **Studio** → activate preset **B** live (or `demo_reset.py --scene sara`).

1. The active specialists change: **Label & Food-Safety Checker** and **Supplier Compliance
   Auditor** replace the Craft Quality Checker, and ordering switches from one-off orders to
   replenishment. Bahasa Indonesia, IDR, P-IRT,
   halal. The gateway's audit log records the new config hash. *"Config, not code."*
2. Sara (`/vendor?vendor=vendor:sara`) → **My shop** → **Apply** to FreshCart; the simulated ID,
   bank and P-IRT registries pass; the approval waits in console → **Inbox** (with the document checks).
3. Her crisps label has **no P-IRT number** → the Label Checker flags
   `MISSING_REGISTRATION_PIRT`. Add the number, and the flag goes away.

## Scene 4: the control plane and attacks (about 1.5 min)

1. Console → **Observability** → **Overview**: supplier, retailer and hosted agents (active,
   live, talking now), messages relayed, model tokens, logins, authentication failures and
   authorisation denials; below, **who talks to whom** (Maya's agent ⇄ FreshCart's, with skills
   and Guardian decisions). Click `vendor-agent:maya` for her detail: trust breakdown, key,
   permissions, logins, denials, timings and her own logs, with **Suspend** right there.
2. **Activities**: how long each end-to-end flow and each step takes, and the tokens it used
   (the Concierge's chat is the only step that spends tokens; the Guardian spends none).
3. **Agents** → open a trace: one order across vendor-svc, gateway and retailer-svc, each span
   tagged with the agent and on whose behalf it acted.
4. **Suspend** the `terms-agent` (operator token), then **Suppliers → Terms** → refused
   (`agent_blocked`); resume it.
5. In a terminal (`--live`, so the attacks hit the running gateway the console is watching):
   ```bash
   uv run python scripts/attack_impostor.py --live      # IMPOSTOR BLOCKED
   uv run python scripts/attack_injected_doc.py --live  # INJECTED DOCUMENT BLOCKED
   ```
   **Overview** counts the authentication failures; **Safety** shows the denials and the
   quarantined uploader. The victim's trust score is unaffected. The injected document is
   uploaded as Sam, so Sam stays quarantined: run `demo_reset.py` before showing his onboarding.

## If something goes wrong

| Symptom | Fix |
|---|---|
| A screen is empty after a reset | Reload the page; the web app does not hold state |
| The job does not resume after a Slack reply | `curl -X POST localhost:8002/dev/tick` (and `:8001` for vendor nudges) |
| Anything else | `uv run python scripts/demo_reset.py [--scene …]`. It takes about a second |
| A service died | `make dev` again. With `VL_STORE=sqlite`, state survives a restart |
| A supplier is not in the Inbox after **Apply** | Only suppliers whose checks all pass go there; a failed document (e.g. Sam's bank `NAME_MISMATCH`) keeps them *verifying* and asks them, in their app, to fix it |

## Lines to have ready

- **"Is the AI signing contracts?"** No. The Concierge has no signing tool. Only the Guardian
  signs: inside her rules, or with her consent bound to the hash of the exact proposal.
- **"What if the model is tricked?"** It can word things, nothing more. Documents and messages are
  screened at the gateway (heuristics plus Model Armor in the cloud), and consent is taken only
  from her own words after the read-back.
- **"How do you add a market?"** Config files: a Business Profile, a Country Pack and a Retailer
  Profile. Config B has no code of its own.
