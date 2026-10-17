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

`DEMO_SCENE=waiting make dev` starts straight into a scene. Start any scene only after the
"VendorLink ready" line; `make dev` resets once on startup and would wipe an earlier one.

- Without `GOOGLE_API_KEY` / model names the Concierge runs in **offline intent mode** (typed
  Telugu or English works; Live voice does not). Say so if asked; everything that decides or
  signs is deterministic either way.
- With `VL_CHANNELS=local` (the default), Slack and Gmail messages land in **Console → Outbox
  (dev)**, where you can reply as the person. With a real workspace (`VL_CHANNELS=mcp`), show
  the Slack message on a phone instead.
- Open the tabs in advance: console **Orders**, **Tasks**, **Suppliers**, **Observability**,
  **Trust**; Maya's **Deals** tab.

## Scene 1: the order loop (Config A, about 3 min)

*"Maya grows spinach, tomatoes and mint on a small farm. She set her rules once, by voice: at most 300
bunches a day, pickup not before 6 am, a price floor, paid within 7 days."*

1. Maya → **Setup**: show her rules, the standing mandate signed by her Guardian with her
   consent.
2. Console → **Orders** → vendor Maya, store **Hub 4**, tomorrow → **Place**. The
   Replenishment Planner asks for **350 bunches, pickup 05:30**.
3. The Guardian returns **red**: `QTY_OVER_DAILY_MAX`, `PICKUP_BEFORE_WINDOW`. It counters with
   **300 @ 06:00** (order v2; v1 is never changed). The retailer's policy auto-accepts the counter
   (within `auto_accept_counter_max_delta`). *"No model decided this. The Guardian is plain code."*
4. Maya → **Deals**: the deal is **amber**, waiting for her. Switch her app to **తెలుగు** with the
   🌐 picker in the header: the read-back, buttons and chat are now in Telugu. *"Her language,
   her choice."* She says **"అవును"** (yes): type it in **Chat**, or tap Yes on the deal.
5. The deal shows **signed**: an Order Mandate whose consent hash matches the exact proposal.
6. Console → **Trust** → **Verify** the audit chain ✓, then **Tamper** a record → verify shows
   the broken seq. *"Every state change is one audit record, hash-chained."*

## Scene 2: agents that ask people (Config A, about 2 min)

```bash
uv run python scripts/demo_reset.py --scene waiting
```

*"Most of this runs as a batch at night. Nobody watches a screen."*

1. Console → **Tasks**: tomorrow's replenishment run is **waiting**. Hub 7 already went out
   (within policy); Hub 4 is over the auto-order limit (the scene saved ₹13 000 in Studio) and
   waits for the buyer.
2. Console → **Outbox (dev)**: the buyer's Slack message ("over the auto-order limit…"). Reply
   **approve** as the buyer. Within a Liaison tick (or `curl -X POST localhost:8002/dev/tick`)
   the job resumes from where it paused (LangGraph checkpoint) and sends the order.
3. Maya → **Deals** → **Questions**: the planner asked, in her language, how much mint she can
   supply. She answers **45**; FreshCart sees it under the supplier.
4. Maya → **Deals** → **Terms**: FreshCart's payment terms (7 days; the scene proposed them,
   live it is **Suppliers → Terms**). Her Guardian checked them against her rules, so terms
   paid later than 7 days could not be accepted. She accepts; console → **Suppliers** shows
   *terms approved*.
5. *"Slack and email may nudge Maya back to the app. They can never carry her consent."*

## Scene 3: same agents, different market (Config B, about 1.5 min)

Console → **Studio** → activate preset **B** live (or `demo_reset.py --scene sara`).

1. The active specialists change: **Label & Food-Safety Checker** and **Supplier Compliance
   Auditor** replace the Produce Grader and Replenishment Planner. Bahasa Indonesia, IDR, P-IRT,
   halal. The gateway's audit log records the new config hash. *"Config, not code."*
2. Sara (`/vendor?vendor=vendor:sara`) → **Setup** → **Apply** to FreshCart; the simulated ID,
   bank and P-IRT registries pass; the approval waits in console → **Queue**.
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
   quarantined uploader. The victim's trust score is unaffected.

## If something goes wrong

| Symptom | Fix |
|---|---|
| A screen is empty after a reset | Reload the page; the web app does not hold state |
| The job does not resume after a Slack reply | `curl -X POST localhost:8002/dev/tick` (and `:8001` for vendor nudges) |
| Anything else | `uv run python scripts/demo_reset.py [--scene …]`. It takes about a second |
| A service died | `make dev` again. With `VL_STORE=sqlite`, state survives a restart |

## Lines to have ready

- **"Is the AI signing contracts?"** No. The Concierge has no signing tool. Only the Guardian
  signs: inside her rules, or with her consent bound to the hash of the exact proposal.
- **"What if the model is tricked?"** It can word things, nothing more. Documents and messages are
  screened at the gateway (heuristics plus Model Armor in the cloud), and consent is taken only
  from her own words after the read-back.
- **"How do you add a market?"** Config files: a Business Profile, a Country Pack and a Retailer
  Profile. Config B has no code of its own.
