# Deploying VendorLink to Google Cloud

**Not needed to run or test locally:** for that, see `docs/LOCAL_SETUP.md`, which also explains
how to get the Gemini, Slack and Gmail keys. This page covers the hosted demo: five Cloud Run
services and the web app on Firebase Hosting. The design decisions are in ADR 0014.

| What | Where |
|---|---|
| `trust-gateway`, `vendor-svc`, `retailer-svc`, `mocks` | Cloud Run, `asia-south1` (`infra/Dockerfile`) |
| `slack-mcp` (Slack for the Liaison) | Cloud Run (`infra/slack_mcp/Dockerfile`) |
| Records, audit log | Firestore (`asia-south1`) |
| Durable job checkpoints | Cloud SQL Postgres `vl-jobs` |
| Signing keys, photos | Cloud KMS keyrings and GCS buckets: one per tenant |
| Screening | Model Armor template `vl-screen` (`us-central1`) |
| Models | Vertex AI in `MODEL_LOCATION` (default `us-central1`) |
| Web app | Firebase Hosting, `https://<PROJECT_ID>.web.app` |

You need: a GCP project with billing, the `gcloud` and `firebase` CLIs (`npm i -g firebase-tools`),
the Slack app from `docs/LOCAL_SETUP.md` C1 (bot token and team id), and model names that exist
on Vertex AI in `MODEL_LOCATION`. Check them there; they can differ from the AI Studio list, and
`LIVE_MODEL` must be a Live API model.

## 1. Bootstrap the project (once)

```bash
gcloud auth login && gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=<PROJECT_ID>
./infra/bootstrap.sh
```

It enables the APIs, then creates:
- the Artifact Registry repository and the Firestore database;
- the service accounts and their roles (`infra/iam.md`);
- the keyrings and buckets, each bound to its own tenant;
- the Model Armor template and Cloud SQL;
- the secrets, with each service allowed only the ones it mounts;
- the Firebase project.

It asks once for the four model names and the two Slack values. Tokens and the database password
are generated and never printed. Re-running it is safe and never rotates an existing secret.

To change a value later: `printf '%s' "<value>" | gcloud secrets versions add <secret> --data-file=-`,
then redeploy (each service reads `latest` when it starts).

## 2. Who is asked on Slack

```bash
cp infra/deploy.env.example infra/deploy.env     # never committed
```

Fill in the Slack channel or user ids for the buyer, the category manager, the category lead and
ops. In Slack, invite the VendorLink bot to each of those channels (`/invite @VendorLink`).

## 3. Build and deploy

```bash
export TAG=$(git rev-parse --short HEAD)                        # deploy a committed tree
export CLOUDSQL_INSTANCE=$GOOGLE_CLOUD_PROJECT:asia-south1:vl-jobs
gcloud builds submit --config infra/cloudbuild.yaml --substitutions=_TAG=$TAG   # tests, then 5 images
./infra/deploy.sh
firebase login && ./infra/deploy_web.sh
```

If the web app is not on `https://<PROJECT_ID>.web.app`, set `WEB_ORIGIN` before `deploy.sh`.
It is the only browser origin the APIs accept, and the base of links in Slack messages.

## 4. Seed and smoke test

```bash
source infra/cloud_env.sh                 # service URLs + admin token; scripts now target the cloud
uv run python scripts/demo_reset.py       # clean config A
for s in $TRUST_GATEWAY_URL $VENDOR_SVC_URL $RETAILER_SVC_URL; do curl -s $s/healthz; echo; done
curl -s -o /dev/null -w '%{http_code}\n' -X POST $RETAILER_SVC_URL/dev/tick   # 404: no token
uv run python scripts/attack_impostor.py --live          # BLOCKED
uv run python scripts/attack_injected_doc.py --live      # BLOCKED (Model Armor)
uv run python scripts/ui_check.py --web https://$GOOGLE_CLOUD_PROJECT.web.app
uv run python scripts/demo_reset.py       # reset after the checks
```

Then run the hero flow by hand (docs/DEMO.md), with the buyer replying **on Slack**. The
`e2e_order_loop.py --live` script answers through the dev outbox, which the hosted demo does not
have. A buyer's reply is read within one Liaison pass (15 s).

- **The deployed console's operator token** (Observability → operator; it also unlocks
  Trust → Tamper): `gcloud secrets versions access latest --secret vl-operator-token`.
- **Demo scenes:** `source infra/cloud_env.sh && uv run python scripts/demo_reset.py --scene waiting`.
- **Logs:** Cloud Logging, `resource.type="cloud_run_revision"`; each line is JSON with `trace_id`.

## Roll back and tear down

```bash
gcloud run revisions list --service retailer-svc --region asia-south1
gcloud run services update-traffic retailer-svc --region asia-south1 --to-revisions <REVISION>=100
firebase hosting:rollback --project $GOOGLE_CLOUD_PROJECT
```

Three services run with one instance always on, plus Cloud SQL. After judging, stop paying:

```bash
for s in trust-gateway vendor-svc retailer-svc mocks slack-mcp; do
  gcloud run services delete $s --region asia-south1 --quiet; done
gcloud sql instances delete vl-jobs --quiet
gcloud scheduler jobs delete vl-replenishment-run --location asia-south1 --quiet
```

KMS keyrings cannot be deleted. They cost nothing without key versions; disable the versions, or
delete the whole project.

## Cloud variable reference

| Variable | Where it comes from |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | your project |
| `GOOGLE_CLOUD_LOCATION` | `MODEL_LOCATION` on vendor/retailer (the models), `asia-south1` on the gateway |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` (set by `deploy.sh`); models via Vertex AI, not the API key |
| `GCS_BUCKET`, `KMS_KEYRING` (full name), `MODEL_ARMOR_TEMPLATE` | created by `bootstrap.sh`, set by `deploy.sh` |
| `VL_ADMIN_TOKEN`, `VL_OPERATOR_TOKEN`, model names | Secret Manager (`bootstrap.sh`) |
| `VL_CHECKPOINT_DSN` | Secret Manager (`bootstrap.sh`, from the generated password) |
| `VL_DEMO_ENDPOINTS=true`, `VL_DEV_ENDPOINTS=false` | `deploy.sh`; `/dev/*` only with the admin token (ADR 0014) |
| `MCP_SLACK_URL`, `vl-mcp-slack-headers` | `deploy.sh` from the `slack-mcp` service; bearer token from `bootstrap.sh` |
| `FRESHCART_*_SLACK`, `FRESHCART_*_EMAIL` | `infra/deploy.env` |
| `VL_CORS_ORIGINS`, `VL_PUBLIC_URL` | set by `deploy.sh` from `WEB_ORIGIN` |
| `REGION`, `MODEL_LOCATION`, `ARMOR_LOCATION` | optional overrides for both scripts |
