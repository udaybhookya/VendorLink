#!/usr/bin/env bash
# Deploy the four services to Cloud Run. Requires: gcloud auth, GOOGLE_CLOUD_PROJECT, TAG.
# Secrets (VL_ADMIN_TOKEN, model keys) come from Secret Manager, never from this file.
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}" "${TAG:?}"
REGION=${REGION:-asia-south1}
REPO=${REPO:-vendorlink}
IMG="${REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${REPO}"

url() { gcloud run services describe "$1" --region "$REGION" --format 'value(status.url)' 2>/dev/null || true; }

deploy() {
  local name=$1 sa=$2; shift 2
  gcloud run deploy "$name" --image "${IMG}/${name}:${TAG}" --region "$REGION" \
    --service-account "${sa}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com" \
    --allow-unauthenticated "$@"
}

# Per-tenant keyrings and buckets plus the Model Armor template (idempotent; see docs/adr/0005).
for ring in vl-gateway vl-vendor vl-retailer; do
  gcloud kms keyrings create "$ring" --location "$REGION" 2>/dev/null || true
done
for bucket in "vl-vendor-${GOOGLE_CLOUD_PROJECT}" "vl-retailer-${GOOGLE_CLOUD_PROJECT}"; do
  gcloud storage buckets create "gs://${bucket}" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention 2>/dev/null || true
done
ARMOR_LOCATION=${ARMOR_LOCATION:-us-central1}
gcloud model-armor templates create vl-screen --location "$ARMOR_LOCATION" \
  --pi-and-jailbreak-filter-settings-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above \
  --malicious-uri-filter-settings-enforcement=enabled 2>/dev/null || true
# The hosted web app (infra/deploy_web.sh): the only browser origin the APIs accept, and the base
# of links sent in Slack/Gmail messages. One origin (gcloud splits env values on commas).
WEB_ORIGIN=${WEB_ORIGIN:-https://${GOOGLE_CLOUD_PROJECT}.web.app}
cloud="GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},VL_CORS_ORIGINS=${WEB_ORIGIN},VL_PUBLIC_URL=${WEB_ORIGIN}"

# The gateway's audit log has a single writer in the MVP (see docs/adr/0001).
deploy trust-gateway vl-gateway --max-instances 1 \
  --set-env-vars "${cloud},VL_STORE=firestore,VL_SIGNER=kms,KMS_KEYRING=vl-gateway,VL_SCREEN=model_armor,MODEL_ARMOR_TEMPLATE=projects/${GOOGLE_CLOUD_PROJECT}/locations/${ARMOR_LOCATION}/templates/vl-screen,VL_DEV_ENDPOINTS=false" \
  --set-secrets "VL_ADMIN_TOKEN=vl-admin-token:latest,VL_OPERATOR_TOKEN=vl-operator-token:latest"
GATEWAY=$(url trust-gateway)
deploy mocks vl-mocks --set-env-vars "VL_DEV_ENDPOINTS=false"
MOCKS=$(url mocks)
common="${cloud},TRUST_GATEWAY_URL=${GATEWAY},MOCKS_URL=${MOCKS},VL_STORE=firestore,VL_BLOB=gcs,VL_SIGNER=kms,VL_DEV_ENDPOINTS=false,VL_CHANNELS=mcp,VL_TRACE=cloud,GOOGLE_GENAI_USE_VERTEXAI=true"
# Durable LangGraph jobs: Cloud SQL Postgres (connection name + DSN secret). Channels: MCP servers
# reached over HTTP (MCP_*_URL) with tokens from Secret Manager.
hitl_secrets="VL_CHECKPOINT_DSN=vl-checkpoint-dsn:latest,MCP_SLACK_HEADERS=vl-mcp-slack-headers:latest,MCP_GMAIL_HEADERS=vl-mcp-gmail-headers:latest"
deploy vendor-svc vl-vendor-tenant --set-env-vars "$common,KMS_KEYRING=vl-vendor,GCS_BUCKET=vl-vendor-${GOOGLE_CLOUD_PROJECT},VL_CHECKPOINTS=memory,MCP_GMAIL_URL=${MCP_GMAIL_URL:-}" \
  --set-secrets "VL_ADMIN_TOKEN=vl-admin-token:latest,FLASH_MODEL=vl-flash-model:latest,PRO_MODEL=vl-pro-model:latest,LIVE_MODEL=vl-live-model:latest,MCP_GMAIL_HEADERS=vl-mcp-gmail-headers:latest"
deploy retailer-svc vl-retailer-tenant \
  --set-env-vars "$common,KMS_KEYRING=vl-retailer,GCS_BUCKET=vl-retailer-${GOOGLE_CLOUD_PROJECT},VL_CHECKPOINTS=postgres,MCP_SLACK_URL=${MCP_SLACK_URL:-},MCP_GMAIL_URL=${MCP_GMAIL_URL:-}" \
  --add-cloudsql-instances "${CLOUDSQL_INSTANCE:?set CLOUDSQL_INSTANCE (project:region:instance)}" \
  --set-secrets "VL_ADMIN_TOKEN=vl-admin-token:latest,FLASH_MODEL=vl-flash-model:latest,PRO_MODEL=vl-pro-model:latest,${hitl_secrets}"
# Each service must know its own public URL for A2A forwarding.
gcloud run services update vendor-svc --region "$REGION" --update-env-vars "VENDOR_SVC_URL=$(url vendor-svc)"
gcloud run services update retailer-svc --region "$REGION" --update-env-vars "RETAILER_SVC_URL=$(url retailer-svc)"
# Nightly replenishment run (durable LangGraph job; pauses for people, resumes on answers).
gcloud scheduler jobs create http vl-replenishment-run --location "$REGION" --schedule "0 22 * * *" \
  --uri "$(url retailer-svc)/retailers/retailer:freshcart/jobs/replenishment-run" --http-method POST \
  --headers "content-type=application/json" \
  --message-body '{"for_date": "tomorrow", "vendor_id": "vendor:maya"}' \
  --oidc-service-account-email "vl-scheduler@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com" 2>/dev/null \
  || echo "scheduler job exists (update it with: gcloud scheduler jobs update http …)"
echo "gateway=${GATEWAY} vendor=$(url vendor-svc) retailer=$(url retailer-svc) mocks=${MOCKS}"
