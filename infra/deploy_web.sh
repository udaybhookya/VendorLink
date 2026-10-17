#!/usr/bin/env bash
# Build the web app against the deployed services and publish it to Firebase Hosting.
# Requires: gcloud auth, firebase CLI (firebase login), GOOGLE_CLOUD_PROJECT. Run after deploy.sh.
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}"
REGION=${REGION:-asia-south1}
url() { gcloud run services describe "$1" --region "$REGION" --format 'value(status.url)'; }

cd "$(dirname "$0")/.."
export VITE_VENDOR_URL=$(url vendor-svc) VITE_RETAILER_URL=$(url retailer-svc) \
  VITE_GATEWAY_URL=$(url trust-gateway)
(cd web && npm ci && npm run build)
firebase deploy --only hosting --project "$GOOGLE_CLOUD_PROJECT"
echo "web: https://${GOOGLE_CLOUD_PROJECT}.web.app (set WEB_ORIGIN for deploy.sh if you use another domain)"
