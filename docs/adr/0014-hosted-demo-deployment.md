# ADR 0014 — Hosted demo deployment

Status: accepted · 18 Oct 2026 · `vl_core/devguard.py`, the three services' `/dev/*` routes,
`scripts/_demo.py`, `infra/` (bootstrap, deploy, Slack MCP image, cloud_env), console nav

## Problem
The deploy scripts (ADR 0005) produced a stack that could not run the demo:

1. **It started empty.** Seeding, demo scenes and the audit-tamper step live only under
   `/dev/*`, and a deployment sets `VL_DEV_ENDPOINTS=false`, so they all returned 404.
2. **Process state on autoscaled services.** The Concierge's sessions, the ADK session service
   and the event bus live in process memory. Two instances would split a vendor's conversation
   and the console's live updates.
3. **The Liaison's tick loop stalled.** It is an asyncio task. Cloud Run throttles the CPU
   between requests by default, so replies, reminders and escalations were never processed
   while nobody was clicking.
4. **Voice dropped after five minutes**, at the default request timeout.
5. **One `GOOGLE_CLOUD_LOCATION` was used for both the models and the KMS keyrings.** The Live
   and image models are not offered in `asia-south1`.
6. **Missing pieces.** `IMAGE_MODEL` and the retailer's Slack contacts were never set. Slack's MCP
   server had no image. Nothing created the service accounts, IAM bindings, secrets or database.

## Decisions
1. **`VL_DEMO_ENDPOINTS`** (default false). When it is true and `VL_DEV_ENDPOINTS` is false:
   - `/dev/reset` and `/dev/tick` answer only a caller with the admin token (`X-VL-Admin-Token`).
   - `/dev/tamper` also accepts the operator token (`X-VL-Operator-Token`), so the console's Trust
     page can show tamper detection after the operator signs in on Observability.
   - The dev outbox stays closed. The hosted demo sends to real Slack.
   - Any other caller gets 404, exactly as before, and the refusal is logged at WARNING.

   `scripts/_demo.py` sends the admin token whenever it has one. `source infra/cloud_env.sh`
   points every demo script at the hosted services.
2. **A demo reset wipes the gateway's `gw.*` collections, including the audit log.** This is the
   one exception to "append-only": it is a demo deployment of synthetic data, only the admin token
   can do it, and a production deploy keeps both flags false. Say so if asked.
3. **One instance per stateful service:**
   - the gateway, vendor-svc and retailer-svc run with `min 1`, `max 1` and the CPU always on
     (`--no-cpu-throttling`);
   - vendor-svc also gets session affinity and a 3600 s timeout, for voice;
   - retailer-svc gets 2 GiB, for image work.

   Durable state is already in Firestore (records) and Cloud SQL (job checkpoints). What a restart
   loses is conversations in progress. Scaling out later means moving sessions and the bus to a
   shared store first.
4. **`MODEL_LOCATION` (default `us-central1`) is the services' `GOOGLE_CLOUD_LOCATION`** (the
   variable the model clients read). Keyrings are passed by full name, so KMS, the buckets,
   Firestore and Cloud SQL stay in `asia-south1`.
5. **The Slack MCP server is a fifth Cloud Run service** (`infra/slack_mcp`, version pinned).
   - Its URL is public, but every call needs its bearer token (`vl-slack-mcp-auth`); our client
     sends the token from `vl-mcp-slack-headers`.
   - Cloud Run IAM would need a second identity token on each call, which the MCP client does not
     send yet.
   - Gmail stays off when deployed: the server it uses has no HTTP mode.
6. **`infra/bootstrap.sh` creates the project's resources, idempotently:**
   - APIs, the Artifact Registry repository and the Firestore database;
   - service accounts, keyrings and buckets, each bound to its own tenant;
   - the Model Armor template;
   - secrets, with per-service access;
   - Cloud SQL and the Firebase project.

   A rerun never rotates a secret. Who is asked on Slack comes from `infra/deploy.env`, which is
   never committed.
7. **The hosted console hides "Outbox (dev)"** (`VITE_HOSTED=true` in `infra/deploy_web.sh`).

## Consequences
- **Cost:** three always-on instances, plus Cloud SQL `db-f1-micro`. Tear the demo down after
  judging (docs/DEPLOY.md, "Tear down").
- **The hosted order loop is checked by hand:** `e2e_order_loop.py --live` answers the buyer
  through the dev outbox, which is closed. Run the hero flow with the buyer replying on Slack.
  The attack scripts, `demo_reset.py --scene …` and `ui_check.py --web <hosted>` all run against
  the hosted demo.
