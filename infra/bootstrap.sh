#!/usr/bin/env bash
# One-time setup of a GCP project for the hosted demo (docs/DEPLOY.md, roles in infra/iam.md).
# Idempotent: re-running creates what is missing and never rotates a secret that exists.
# Requires: gcloud auth as a project owner, GOOGLE_CLOUD_PROJECT. Optional: firebase CLI.
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}"
P=${GOOGLE_CLOUD_PROJECT}
REGION=${REGION:-asia-south1}
ARMOR_LOCATION=${ARMOR_LOCATION:-us-central1}
REPO=${REPO:-vendorlink}
SQL_INSTANCE=${SQL_INSTANCE:-vl-jobs}
gcloud config set project "$P" >/dev/null
NUMBER=$(gcloud projects describe "$P" --format 'value(projectNumber)')
sa() { echo "serviceAccount:$1@${P}.iam.gserviceaccount.com"; }
quiet() { "$@" >/dev/null 2>&1 || true; }  # create-if-missing; real failures show on deploy
step() { echo "== $*"; }

step "APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  firestore.googleapis.com cloudkms.googleapis.com storage.googleapis.com modelarmor.googleapis.com \
  aiplatform.googleapis.com secretmanager.googleapis.com sqladmin.googleapis.com \
  cloudscheduler.googleapis.com cloudtrace.googleapis.com firebase.googleapis.com \
  firebasehosting.googleapis.com

step "Artifact Registry, Firestore"
quiet gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"
quiet gcloud firestore databases create --location "$REGION"
# Cloud Build pushes images (new projects build as the Compute default SA, older ones as Cloud Build's).
for builder in "${NUMBER}-compute@developer.gserviceaccount.com" "${NUMBER}@cloudbuild.gserviceaccount.com"; do
  for role in roles/artifactregistry.writer roles/logging.logWriter; do
    quiet gcloud projects add-iam-policy-binding "$P" --member "serviceAccount:${builder}" --role "$role" --condition None
  done
done

step "Service accounts and project roles"
for name in vl-gateway vl-vendor-tenant vl-retailer-tenant vl-mocks vl-scheduler vl-slack-mcp; do
  quiet gcloud iam service-accounts create "$name" --display-name "VendorLink ${name#vl-}"
done
bind() { quiet gcloud projects add-iam-policy-binding "$P" --member "$(sa "$1")" --role "$2" --condition None; }
for name in vl-gateway vl-vendor-tenant vl-retailer-tenant; do
  bind "$name" roles/datastore.user
  bind "$name" roles/cloudtrace.agent
done
bind vl-vendor-tenant roles/aiplatform.user
bind vl-retailer-tenant roles/aiplatform.user
bind vl-retailer-tenant roles/cloudsql.client
bind vl-gateway roles/modelarmor.user
bind vl-scheduler roles/run.invoker

step "KMS keyrings (each tenant signs only with its own)"
for pair in vl-gateway:vl-gateway vl-vendor:vl-vendor-tenant vl-retailer:vl-retailer-tenant; do
  ring=${pair%%:*} owner=${pair#*:}
  quiet gcloud kms keyrings create "$ring" --location "$REGION"
  for role in roles/cloudkms.signerVerifier roles/cloudkms.admin; do  # keys are created on first use
    quiet gcloud kms keyrings add-iam-policy-binding "$ring" --location "$REGION" --member "$(sa "$owner")" --role "$role"
  done
done

step "Buckets (each tenant reads and writes only its own)"
for pair in vl-vendor:vl-vendor-tenant vl-retailer:vl-retailer-tenant; do
  bucket="gs://${pair%%:*}-${P}" owner=${pair#*:}
  quiet gcloud storage buckets create "$bucket" --location "$REGION" --uniform-bucket-level-access --public-access-prevention
  quiet gcloud storage buckets add-iam-policy-binding "$bucket" --member "$(sa "$owner")" --role roles/storage.objectAdmin
done

step "Model Armor template vl-screen ($ARMOR_LOCATION)"
quiet gcloud model-armor templates create vl-screen --location "$ARMOR_LOCATION" \
  --pi-and-jailbreak-filter-settings-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above \
  --malicious-uri-filter-settings-enforcement=enabled

step "Secrets"
has() { gcloud secrets describe "$1" >/dev/null 2>&1; }
put() { printf '%s' "$2" | gcloud secrets create "$1" --data-file=- >/dev/null; echo "   created $1"; }
token() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
ask() {  # ask() <secret> <prompt>: only when the secret does not exist yet
  has "$1" && return
  local value=""; while [ -z "$value" ]; do read -r -s -p "   $2: " value; echo; done
  put "$1" "$value"
}
for s in vl-admin-token vl-operator-token vl-slack-mcp-auth; do has "$s" || put "$s" "$(token)"; done
ask vl-flash-model "FLASH_MODEL (as named on Vertex AI)"
ask vl-pro-model "PRO_MODEL"
ask vl-live-model "LIVE_MODEL (a Live API model)"
ask vl-image-model "IMAGE_MODEL (AI product photos)"
ask vl-slack-bot-token "SLACK_BOT_TOKEN (xoxb-…, docs/LOCAL_SETUP.md C1)"
ask vl-slack-team-id "SLACK_TEAM_ID (T…)"
has vl-mcp-slack-headers || put vl-mcp-slack-headers \
  "{\"Authorization\": \"Bearer $(gcloud secrets versions access latest --secret vl-slack-mcp-auth)\"}"
has vl-mcp-gmail-headers || put vl-mcp-gmail-headers '{}'  # Gmail is off when deployed

step "Cloud SQL Postgres $SQL_INSTANCE (durable jobs; takes a few minutes the first time)"
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --edition=enterprise \
    --region "$REGION" --tier=db-f1-micro
fi
quiet gcloud sql databases create vl_jobs --instance "$SQL_INSTANCE"
if ! has vl-checkpoint-dsn; then
  password=$(token)
  gcloud sql users create vl --instance "$SQL_INSTANCE" --password "$password" >/dev/null 2>&1 \
    || gcloud sql users set-password vl --instance "$SQL_INSTANCE" --password "$password" >/dev/null
  put vl-checkpoint-dsn "postgresql://vl:${password}@/vl_jobs?host=/cloudsql/${P}:${REGION}:${SQL_INSTANCE}"
fi

step "Secret access (each service only the secrets it mounts)"
grant() {
  local owner=$1; shift
  for s in "$@"; do
    quiet gcloud secrets add-iam-policy-binding "$s" --member "$(sa "$owner")" --role roles/secretmanager.secretAccessor
  done
}
grant vl-gateway vl-admin-token vl-operator-token
grant vl-vendor-tenant vl-admin-token vl-flash-model vl-pro-model vl-live-model vl-mcp-gmail-headers
grant vl-retailer-tenant vl-admin-token vl-flash-model vl-pro-model vl-image-model vl-checkpoint-dsn \
  vl-mcp-slack-headers vl-mcp-gmail-headers
grant vl-slack-mcp vl-slack-bot-token vl-slack-team-id vl-slack-mcp-auth

step "Firebase Hosting"
if command -v firebase >/dev/null; then
  quiet firebase projects:addfirebase "$P"
else
  echo "   firebase CLI not found: npm i -g firebase-tools, then: firebase projects:addfirebase $P"
fi

echo "bootstrap done. Next: docs/DEPLOY.md step 2"
