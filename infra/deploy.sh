#!/usr/bin/env bash
# Deploy the hosted demo to Cloud Run: the four services plus the Slack MCP server.
# Requires: infra/bootstrap.sh run once, images built (infra/cloudbuild.yaml), gcloud auth,
# GOOGLE_CLOUD_PROJECT, TAG, CLOUDSQL_INSTANCE. Optional: infra/deploy.env (who is asked on Slack).
# Secrets (tokens, model names) come from Secret Manager, never from this file. docs/DEPLOY.md.
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}" "${TAG:?}" "${CLOUDSQL_INSTANCE:?set CLOUDSQL_INSTANCE (project:region:instance)}"
cd "$(dirname "$0")/.."
REGION=${REGION:-asia-south1}
# Vertex AI location for the models (Live and image models are not offered in every region).
MODEL_LOCATION=${MODEL_LOCATION:-us-central1}
ARMOR_LOCATION=${ARMOR_LOCATION:-us-central1}
REPO=${REPO:-vendorlink}
P=${GOOGLE_CLOUD_PROJECT}
IMG="${REGION}-docker.pkg.dev/${P}/${REPO}"
# Real Slack ids / emails of the demo retailer's people (config/contacts/retailer_freshcart.json).
[ -f infra/deploy.env ] && . infra/deploy.env

url() { gcloud run services describe "$1" --region "$REGION" --format 'value(status.url)' 2>/dev/null || true; }

deploy() {
  local name=$1 sa=$2; shift 2
  gcloud run deploy "$name" --image "${IMG}/${name}:${TAG}" --region "$REGION" \
    --service-account "${sa}@${P}.iam.gserviceaccount.com" \
    --allow-unauthenticated "$@"
}
ring() { echo "projects/${P}/locations/${REGION}/keyRings/$1"; }

# The hosted web app (infra/deploy_web.sh): the only browser origin the APIs accept, and the base
# of links sent in Slack/Gmail messages. One origin (gcloud splits env values on commas).
WEB_ORIGIN=${WEB_ORIGIN:-https://${P}.web.app}
cloud="GOOGLE_CLOUD_PROJECT=${P},VL_CORS_ORIGINS=${WEB_ORIGIN},VL_PUBLIC_URL=${WEB_ORIGIN}"
# Hosted demo: /dev/reset|tick|tamper only with the admin token (tamper: operator), ADR 0014.
demo="VL_DEV_ENDPOINTS=false,VL_DEMO_ENDPOINTS=true"

# Scaling (ADR 0014). min 1 and CPU always on: the Liaison's tick loop runs between requests.
# max 1: the gateway's audit log has one writer (ADR 0001); the vendor and retailer services keep
# conversations (Concierge / ADK sessions) and the live event stream in process memory.
single="--min-instances 1 --max-instances 1 --no-cpu-throttling"

# The gateway's audit log has a single writer in the MVP (see docs/adr/0001).
deploy trust-gateway vl-gateway $single \
  --set-env-vars "${cloud},GOOGLE_CLOUD_LOCATION=${REGION},${demo},VL_STORE=firestore,VL_SIGNER=kms,KMS_KEYRING=$(ring vl-gateway),VL_SCREEN=model_armor,MODEL_ARMOR_TEMPLATE=projects/${P}/locations/${ARMOR_LOCATION}/templates/vl-screen,VL_TRACE=cloud" \
  --set-secrets "VL_ADMIN_TOKEN=vl-admin-token:latest,VL_OPERATOR_TOKEN=vl-operator-token:latest"
GATEWAY=$(url trust-gateway)
deploy mocks vl-mocks --set-env-vars "VL_DEV_ENDPOINTS=false"
MOCKS=$(url mocks)

# Slack MCP server (infra/slack_mcp). Public URL, but every call needs its bearer token
# (vl-slack-mcp-auth; our client sends it from vl-mcp-slack-headers). Sessions live in memory.
deploy slack-mcp vl-slack-mcp --max-instances 1 --timeout 300 \
  --set-secrets "SLACK_BOT_TOKEN=vl-slack-bot-token:latest,SLACK_TEAM_ID=vl-slack-team-id:latest,AUTH_TOKEN=vl-slack-mcp-auth:latest"
MCP_SLACK_URL="$(url slack-mcp)/mcp"

common="${cloud},GOOGLE_CLOUD_LOCATION=${MODEL_LOCATION},${demo},TRUST_GATEWAY_URL=${GATEWAY},MOCKS_URL=${MOCKS},VL_STORE=firestore,VL_BLOB=gcs,VL_SIGNER=kms,VL_CHANNELS=mcp,VL_HITL_TICK_SECONDS=15,VL_TRACE=cloud,GOOGLE_GENAI_USE_VERTEXAI=true"
models="FLASH_MODEL=vl-flash-model:latest,PRO_MODEL=vl-pro-model:latest"
# Gmail has no HTTP MCP server: off when deployed (MCP_GMAIL_URL empty); Slack carries approvals.
# Voice: one WebSocket per session, so a long request timeout and the same instance throughout.
deploy vendor-svc vl-vendor-tenant $single --session-affinity --timeout 3600 --memory 1Gi \
  --set-env-vars "$common,KMS_KEYRING=$(ring vl-vendor),GCS_BUCKET=vl-vendor-${P},VL_CHECKPOINTS=memory,MCP_GMAIL_URL=${MCP_GMAIL_URL:-}" \
  --set-secrets "VL_ADMIN_TOKEN=vl-admin-token:latest,${models},LIVE_MODEL=vl-live-model:latest,MCP_GMAIL_HEADERS=vl-mcp-gmail-headers:latest"

contacts=""
for v in FRESHCART_BUYER_SLACK FRESHCART_BUYER_EMAIL FRESHCART_CATEGORY_SLACK FRESHCART_CATEGORY_EMAIL \
  FRESHCART_CATEGORY_LEAD_SLACK FRESHCART_CATEGORY_LEAD_EMAIL FRESHCART_OPS_SLACK FRESHCART_OPS_EMAIL; do
  [ -n "${!v:-}" ] && contacts="${contacts},${v}=${!v}"
done
# Durable LangGraph jobs: Cloud SQL Postgres (connection name + DSN secret). Image work: memory.
deploy retailer-svc vl-retailer-tenant $single --timeout 900 --memory 2Gi \
  --set-env-vars "$common,KMS_KEYRING=$(ring vl-retailer),GCS_BUCKET=vl-retailer-${P},VL_CHECKPOINTS=postgres,MCP_SLACK_URL=${MCP_SLACK_URL},MCP_GMAIL_URL=${MCP_GMAIL_URL:-}${contacts}" \
  --add-cloudsql-instances "${CLOUDSQL_INSTANCE}" \
  --set-secrets "VL_ADMIN_TOKEN=vl-admin-token:latest,${models},IMAGE_MODEL=vl-image-model:latest,VL_CHECKPOINT_DSN=vl-checkpoint-dsn:latest,MCP_SLACK_HEADERS=vl-mcp-slack-headers:latest,MCP_GMAIL_HEADERS=vl-mcp-gmail-headers:latest"

# Each service must know its own public URL for A2A forwarding.
gcloud run services update vendor-svc --region "$REGION" --update-env-vars "VENDOR_SVC_URL=$(url vendor-svc)"
gcloud run services update retailer-svc --region "$REGION" --update-env-vars "RETAILER_SVC_URL=$(url retailer-svc)"

# Nightly replenishment run (durable LangGraph job; pauses for people, resumes on answers).
gcloud scheduler jobs create http vl-replenishment-run --location "$REGION" --schedule "0 22 * * *" \
  --uri "$(url retailer-svc)/retailers/retailer:freshcart/jobs/replenishment-run" --http-method POST \
  --headers "content-type=application/json" \
  --message-body '{"for_date": "tomorrow", "vendor_id": "vendor:maya"}' \
  --oidc-service-account-email "vl-scheduler@${P}.iam.gserviceaccount.com" 2>/dev/null \
  || echo "scheduler job exists (update it with: gcloud scheduler jobs update http …)"
echo "gateway=${GATEWAY} vendor=$(url vendor-svc) retailer=$(url retailer-svc) mocks=${MOCKS} slack-mcp=${MCP_SLACK_URL}"
