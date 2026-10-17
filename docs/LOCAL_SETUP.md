# Run VendorLink locally, end to end

Everything runs on your machine; nothing is deployed. Work through the levels in order. Each level
ends with checks, so you know it works before adding the next.

| Level | What you get | Keys needed | Time |
|---|---|---|---|
| **A. No keys** | the whole system: order loop, Guardian, gateway, human-in-the-loop via the dev outbox, both configs, observability | none | 15 min |
| **B. + Gemini** | catalogue from photos and words, produce grading, label reading, the Concierge chat, voice | Gemini API key | +10 min |
| **C. + Slack (and Gmail)** | questions and approvals reach real people in Slack; email nudges | Slack app (+ Google OAuth) | +20 min |

> **`.env` rules:** never commit it (it is in `.gitignore`). Never paste keys into config files,
> code, chat or screenshots. A variable already set in your shell wins over `.env`. Restart
> `make dev` after every change.

---

## 1. Prerequisites

| Tool | Check | Install (macOS) |
|---|---|---|
| uv (installs Python 3.12 for you) | `uv --version` | `brew install uv` |
| Node.js 20 or newer | `node --version` | `brew install node` |
| Google Chrome (browser check) | it opens | <https://www.google.com/chrome/> |
| curl, git | `curl --version` | preinstalled |

## 2. Install

```bash
make install            # Python packages (uv sync) + web packages (npm install)
cp -n .env.example .env # creates .env only if you don't have one
```

If you already had a `.env`, it may be older than `.env.example`. This lists the keys you're
missing:

```bash
comm -13 <(grep -oE '^[A-Z_]+=' .env | sort) <(grep -oE '^[A-Z_]+=' .env.example | sort)
```

Copy the missing lines across, then make sure these local values are set:

```dotenv
VL_STORE=sqlite          # state survives restarts (memory = lost on every restart)
VL_CHECKPOINTS=sqlite    # paused batch jobs survive restarts
VL_CHANNELS=local        # Slack/Gmail messages go to the dev outbox (Level C switches this)
VL_DEV_ENDPOINTS=true    # demo reset and outbox; local only
```

---

## Level A: no keys

### A1. Automated checks (no services needed)

These build the four services in-process and drive them over HTTP:

```bash
make check        # lint + types + ~245 tests (no model calls, even with keys set)
make api-check    # every route of every service is called; must print API CHECK PASSED
make e2e          # order loop green / amber / red; must print ORDER LOOP PASSED
make attacks      # must print IMPOSTOR BLOCKED and INJECTED DOCUMENT BLOCKED
```

### A2. Start the stack

```bash
make dev
```

Wait for **`VendorLink ready: http://localhost:5173`**. `make dev` resets and seeds config A
once at startup. The vendor app opens in **English**; the 🌐 picker in its header switches a
vendor to her own language (Telugu in India, Bahasa Indonesia in Indonesia).

| What | URL |
|---|---|
| Landing page | <http://localhost:5173> |
| Vendor app (laptop layout; narrower than about 1000 px = phone layout) | <http://localhost:5173/vendor?vendor=vendor:maya> |
| Retailer console | <http://localhost:5173/retailer> |
| Service health | `curl localhost:8000/healthz` (gateway), `:8001` (vendor), `:8002` (retailer), `:8009` (mocks) |

**Use them as apps (optional, nicer for recording):** in Chrome, open each URL and click the
**Install** icon in the address bar (or ⋮ → Cast, save and share → Install page as app). You
get "VendorLink" and "VendorLink Console", each in its own window with its own icon. Remove one
any time from `chrome://apps`.

### A3. Browser check of every screen

In a second terminal, while `make dev` runs:

```bash
make ui-check     # 23 checks in headless Chrome (phone and laptop layouts, installability); must print UI CHECK PASSED
```

Screenshots land in `.vl_data/ui/`.

### A4. What to expect without keys

Everything that decides, signs or guards is deterministic, so it works fully. Without a model:
- spoken or typed listings are parsed without the model (flag `PARSED_WITHOUT_MODEL`);
- photo grading ends in "needs review";
- the Concierge answers in **offline intent mode**: typed Telugu or English such as "my deals",
  "yes" or "questions" works, and free conversation does not;
- voice is off.

Slack and Gmail messages appear in **Console → Outbox (dev)**, where you can reply as the person.

---

## Level B: Gemini

### B1. Get an API key

1. Open **Google AI Studio → API keys**: <https://aistudio.google.com/apikey>.
2. **Create API key** and pick (or create) a Google Cloud project for it.
3. Recommended: in Google Cloud Console → APIs & Services → Credentials, open the key and
   restrict it to the **Generative Language API**.

```dotenv
GOOGLE_API_KEY=<your key>
GOOGLE_GENAI_USE_VERTEXAI=false
```

### B2. Pick the three models

Model names change often, so list the ones your key can use:

```bash
KEY=<your key>
curl -s "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=$KEY" | python3 -c '
import json, sys
for m in json.load(sys.stdin)["models"]:
    ways = m.get("supportedGenerationMethods", [])
    kind = "live" if "bidiGenerateContent" in ways else "text" if "generateContent" in ways else ""
    if kind:
        print(kind.ljust(6), m["name"].split("/", 1)[1])'
```

| Variable | Pick | Used by |
|---|---|---|
| `FLASH_MODEL` | the newest **stable** `text` model with `flash` in the name (not `lite`, `tts`, `image` or `preview`) | catalogue builder, produce grader, label reader |
| `PRO_MODEL` | the newest `text` model with `pro` in the name | the Concierge's chat (Google ADK) |
| `LIVE_MODEL` | a `live` model (supports `bidiGenerateContent`) | voice: vendor app → Chat → hold the 🎙️ button |

> Avoid `gemini-2.0-*` and `gemini-2.5-*`. Google's schedule retires the 2.5 family on
> 16 Oct 2026 and the 2.0 Flash models are already retired. A preview `live` model is fine.

```dotenv
FLASH_MODEL=<name from the list>
PRO_MODEL=<name from the list>
LIVE_MODEL=<name from the list>
```

### B3. Check

```bash
uv run python -m agents llm-check
```

Expect `✓ flash`, `✓ pro` (each answers with the Telugu word for "yes") and `✓ live`. Then
restart `make dev`.

| Message | Fix |
|---|---|
| `API key not valid` | Re-copy the key, with no quotes or spaces |
| `404 … not found` | The model name is wrong or retired; pick again from the list |
| `429` | Free-tier rate limit; wait a minute or enable billing on the project |

### B4. Photos for the grader (optional, for the eval score)

The Produce Grader eval needs 10 photos in `seed/photos/` with exactly these names (expected
grade in brackets):

| File | Grade | File | Grade |
|---|---|---|---|
| `spinach_fresh.jpg` | A | `tomato_spoiled.jpg` | C |
| `spinach_day_old.jpg` | A or B | `mint_fresh.jpg` | A |
| `spinach_wilting.jpg` | C | `tomato_bruised.jpg` | B or C |
| `tomato_fresh.jpg` | A | `carrot_fresh.jpg` | A |
| `tomato_mixed.jpg` | B | `not_produce.jpg` (anything that isn't produce) | C |

Then run `make evals` and aim for 8/10 or better. For the label demo, also keep one or two photos
of packaged-food labels handy: one with a P-IRT number, one without.

---

## Level C: Slack (and Gmail)

VendorLink reaches people through **MCP servers**, started with `npx` (comes with Node.js).

### C1. Slack app

We use [`@zencoderai/slack-mcp-server`](https://www.npmjs.com/package/@zencoderai/slack-mcp-server):
its tool names match `config/channels/slack.json`. (The original reference server is deprecated.)

1. Go to <https://api.slack.com/apps> → **Create New App** → **From a manifest** (under "Or
   start your own way"; skip the **AI agent** and **Starter app** templates, they add features
   VendorLink does not use). Replace the editor's contents with the manifest below (JSON), then
   under **Workspace** pick the one you will demo in (it cannot be changed later, and the
   channels in step 5 must be in it), then **Next** → review → **Create**. If the list is empty
   or your work workspace blocks app installs, create a free one at
   <https://slack.com/get-started#/createnew>, then reload and start again.
   ```json
   {
     "display_information": { "name": "VendorLink" },
     "features": {
       "bot_user": { "display_name": "VendorLink", "always_online": true }
     },
     "oauth_config": {
       "scopes": {
         "bot": [
           "chat:write", "channels:read", "channels:history",
           "groups:read", "groups:history",
           "users:read", "users.profile:read", "reactions:write"
         ]
       }
     },
     "settings": {
       "org_deploy_enabled": false,
       "socket_mode_enabled": false,
       "token_rotation_enabled": false
     }
   }
   ```
   (`groups:*` are only needed for private channels; keeping them is harmless.)
2. If you picked **Blank app** instead: name it `VendorLink`, pick the workspace, then go to
   **OAuth & Permissions → Scopes → Bot Token Scopes** and add the same eight scopes by hand.
   Adding the first scope creates the bot user.
3. **Install App** (left sidebar) → **Install to *your workspace*** → **Allow**. Copy the
   **Bot User OAuth Token** (it starts with `xoxb-`); it is shown on **Install App** and on
   **OAuth & Permissions**.
   If Slack then shows a **"VendorLink is ready!"** dialog with Slack CLI steps (`slack login`,
   `slack create --template …`, `slack run`), close it: those build a separate Bolt starter app.
   VendorLink only needs the bot token; it reaches Slack through the MCP server. You also do not
   need the `xapp-` App token (that is for Socket Mode).
4. Find the team ID:
   ```bash
   curl -s -H "Authorization: Bearer xoxb-YOUR-TOKEN" https://slack.com/api/auth.test
   ```
   Copy `team_id` from the answer (it starts with `T`).
5. Create three channels, for example `#freshcart-buying`, `#freshcart-catalogue` and
   `#freshcart-ops`. In **each** one:
   - type `/invite @VendorLink`;
   - open the channel name → **About** and copy the **Channel ID** (it starts with `C`). Use the
     ID, not the `#name`.

```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_TEAM_ID=T...
MCP_SLACK_TRANSPORT=stdio
MCP_SLACK_COMMAND=npx
MCP_SLACK_ARGS='["-y", "@zencoderai/slack-mcp-server"]'
MCP_SLACK_ENV_PASSTHROUGH=SLACK_BOT_TOKEN,SLACK_TEAM_ID

FRESHCART_BUYER_SLACK=C...        # order approvals over the auto-order limit, counters
FRESHCART_CATEGORY_SLACK=C...     # new suppliers, listing reviews
FRESHCART_OPS_SLACK=C...          # escalations when nobody answers in time
```

**Check:** `uv run python -m agents mcp-tools SLACK` lists `slack_post_message`,
`slack_reply_to_thread` and `slack_get_thread_replies`. The first run downloads the package, so
it can take a minute.

### C2. Gmail (optional)

[`@gongrzhe/server-gmail-autoauth-mcp`](https://www.npmjs.com/package/@gongrzhe/server-gmail-autoauth-mcp)
sends mail from a Google account you sign in with once.

> **Limitation:** this server can't return reply bodies, so VendorLink **sends** through it
> (reminders, escalations, nudges that link back to the app) but does not read email replies.
> People answer on Slack or through the link in the email.

1. Google Cloud Console (the Gemini project is fine) → **APIs & Services → Library** → enable the
   **Gmail API**.
2. **OAuth consent screen**: External, app name `VendorLink`, your email as contact. Leave it in
   **Testing** and add your Gmail address under **Test users**.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → **Download JSON**, then:
   ```bash
   mkdir -p ~/.gmail-mcp && mv ~/Downloads/client_secret_*.json ~/.gmail-mcp/gcp-oauth.keys.json
   npx -y @gongrzhe/server-gmail-autoauth-mcp auth     # browser opens: sign in, allow
   ```

```dotenv
MCP_GMAIL_TRANSPORT=stdio
MCP_GMAIL_COMMAND=npx
MCP_GMAIL_ARGS='["-y", "@gongrzhe/server-gmail-autoauth-mcp"]'
MCP_GMAIL_ENV_PASSTHROUGH=GMAIL_OAUTH_PATH,GMAIL_CREDENTIALS_PATH
GMAIL_OAUTH_PATH=/Users/<you>/.gmail-mcp/gcp-oauth.keys.json     # full paths, no ~
GMAIL_CREDENTIALS_PATH=/Users/<you>/.gmail-mcp/credentials.json

FRESHCART_BUYER_EMAIL=you+buyer@gmail.com       # +tags all arrive in your own inbox
FRESHCART_CATEGORY_EMAIL=you+category@gmail.com
FRESHCART_OPS_EMAIL=you+ops@gmail.com
```

**Check:** `uv run python -m agents mcp-tools GMAIL` lists `send_email` and `search_emails`.

### C3. Switch channels on

```dotenv
VL_CHANNELS=mcp
VL_HITL_TICK_SECONDS=15
```

Restart `make dev`. To go back to the outbox at any time, set `VL_CHANNELS=local`.

---

## 3. End-to-end walkthrough

Do these with `make dev` running, after "VendorLink ready". Put the vendor app (narrow window)
and the console side by side. `docs/DEMO.md` has the screen-by-screen script; this is the
checklist of what must happen.

| # | Do | Expect | Level |
|---|---|---|---|
| 1 | Console → **Orders**: Maya, Hub 4, tomorrow → Place | **red**: the Guardian counters 300 @ 06:00 (v2); the retailer auto-accepts | A |
| 2 | Vendor app → **Deals**: read the read-back (English by default; 🌐 → తెలుగు switches everything to Telugu) → tap Yes, or type **yes** / **అవును** in Chat | deal **signed**; console → Trust shows the mandate | A |
| 3 | Console → **Trust** → Verify, then Tamper → Verify | ✓ verified, then the broken record's number | A |
| 4 | `uv run python scripts/demo_reset.py --scene waiting` | console → **Tasks**: run waiting on the buyer; Maya → Deals: a question about mint, plus FreshCart's terms | A |
| 5 | Answer the buyer: **Outbox (dev)** → reply `approve` (Level A), or reply `approve` in the Slack thread (Level C) | within about 15 s the job resumes and the Hub 4 order goes out | A / C |
| 6 | Maya answers the mint question (`45`) and accepts the terms | console → Tasks → responses shows 45; Suppliers shows *terms approved* | A |
| 7 | Vendor app → **Products**: photo of spinach + "50 bunches spinach at 40 rupees" | a draft listing with a grade and price; flags if the photo is poor | B |
| 8 | Vendor app → **Chat**: type "what deals do I have?"; switch 🌐 to తెలుగు and hold 🎙️ to ask again by voice | the Concierge answers from her real deals, in the language she picked, by text and voice | B |
| 9 | Console → **Studio** → activate preset **B** (or `demo_reset.py --scene sara`) | specialists switch to the label checker and compliance auditor; Sara's label is flagged `MISSING_REGISTRATION_PIRT` | A |
| 10 | Sara (`/vendor?vendor=vendor:sara`) → **Products**: photo of a real food label | label fields read from the photo; P-IRT checked | B |
| 11 | Vendor app → vendor menu → **new vendor** → join → Setup: rules + apply | approval request in console → Queue (and in Slack at Level C) | A / C |
| 12 | Console → **Observability**: operator token from `.env` → suspend `terms-agent` → Suppliers → Terms | refused (`agent_blocked`); resume it and it works again | A |
| 13 | `make attacks` | both BLOCKED; Observability → Safety shows the denials | A |

To start over at any point: `uv run python scripts/demo_reset.py` (or `--scene waiting|sara`).

---

## 4. Reading the logs

`make dev` prints every service's log in one terminal, one line per event:

```
12:04:48.289 INFO  vendor-svc    agents        guardian guardian.decide  status=red reasons=QTY_OVER_DAILY_MAX,PICKUP_BEFORE_WINDOW  vendor:maya · 689482c9
12:04:48.289 INFO  vendor-svc    vendor        deal ord-hub-4-… v1 (350 bunch spinach): Guardian says red → countered  counter="300 bunch spinach"
12:04:48.289 INFO  trust-gateway gateway       relay order.propose: retailer:freshcart → vendor:maya  status=red ms=2
12:04:48.290 INFO  retailer-svc  retailer      counter accepted by policy  order_id=ord-hub-4-… delta=14%
```

Columns: time, level, service, area, what happened, details, then who it was for and the trace
id. **The trace id ties one action together across all four services**: copy it and
`grep` for it, or open it in the console's Observability page.

| You want to see | Look for |
|---|---|
| Which backends and models a service runs with | the `… starting` line each service prints first |
| Why an order got its colour | `guardian.decide … reasons=` and `deal … Guardian says` |
| Messages between vendor and retailer | `relay <skill>: <from> → <to>` (gateway) |
| Model calls | `llm <Schema> ok` (with ms), `rejected` (with the reason), `adk turn`, `chat turn mode=llm\|offline` |
| Questions to people | `asked <role> on <channel>`, `answer recorded`, `liaison tick`, `no contact for role` |
| Blocked or refused requests | `refused: <code>` and `security event … blocked` (WARNING) |
| Crashes | `ERROR … failed` with the traceback underneath |

Set `LOG_LEVEL=DEBUG` in `.env` to also see routine reads and the calls between services.
On Cloud Run the same events come out as JSON (`VL_LOG_FORMAT=auto` picks JSON when there is
no terminal), so Cloud Logging can filter them by field.

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| A scene vanished right after starting | You started it before "VendorLink ready"; the startup reset wiped it. Run it again |
| `address already in use` | Something holds a port: `lsof -iTCP:8001 -sTCP:LISTEN`, then stop it |
| Changes in `.env` have no effect | Restart `make dev`; check your shell doesn't already export the same variable |
| State is lost on restart | `VL_STORE=sqlite` and `VL_CHECKPOINTS=sqlite` in `.env` |
| Slack `not_in_channel` | `/invite @VendorLink` in that channel |
| Slack `channel_not_found` | Use the channel **ID** (`C…`), not `#name` |
| Slack `invalid_auth` | Re-copy the `xoxb-` token; reinstall the app if you changed scopes |
| Nothing arrives in Slack | `VL_CHANNELS=mcp`? `mcp-tools SLACK` works? Then `curl -X POST localhost:8002/dev/tick` |
| Gmail `invalid_grant` | Run the `auth` command again (test-user tokens expire after 7 days) |
| Grading says "needs review" | `FLASH_MODEL` empty or failing: run `python -m agents llm-check` |
| Voice button does nothing | `LIVE_MODEL` empty, or the browser blocked the microphone (allow it for localhost) |
| Start completely fresh | Stop `make dev`, `rm -rf .vl_data` (local data and local signing keys), start again |

## 6. Local variable reference

| Variable | Level | Value |
|---|---|---|
| `VL_STORE`, `VL_CHECKPOINTS` | A | `sqlite` |
| `VL_CHANNELS` | A / C | `local`, then `mcp` |
| `VL_DEV_ENDPOINTS` | A | `true` |
| `LOG_LEVEL`, `VL_LOG_FORMAT` | A | `INFO` (or `DEBUG`), `auto` |
| `VL_ADMIN_TOKEN`, `VL_OPERATOR_TOKEN` | A | the `.env.example` defaults work locally; to change them, run `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI` | B | your key, `false` |
| `FLASH_MODEL`, `PRO_MODEL`, `LIVE_MODEL` | B | from the models list |
| `SLACK_BOT_TOKEN`, `SLACK_TEAM_ID`, `MCP_SLACK_*` | C | see C1 |
| `FRESHCART_*_SLACK` | C | channel IDs `C…` |
| `MCP_GMAIL_*`, `GMAIL_*_PATH`, `FRESHCART_*_EMAIL` | C | see C2 |

Leave the cloud variables (`GOOGLE_CLOUD_*`, `GCS_BUCKET`, `KMS_KEYRING`,
`MODEL_ARMOR_TEMPLATE`, `VL_CHECKPOINT_DSN`) empty locally. Deploying comes later:
`docs/DEPLOY.md`.
