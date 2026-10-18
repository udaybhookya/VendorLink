#!/usr/bin/env bash
# Build the web app against the deployed services and publish it to Firebase Hosting.
# Requires: gcloud auth, firebase CLI (firebase login), GOOGLE_CLOUD_PROJECT. Run after deploy.sh.
# Optional: FIREBASE_SITE (env or infra/deploy.env), a Hosting site you created
# (https://<site>.web.app); default: the project's own site. deploy.sh reads the same value,
# so the APIs accept that origin.
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}"
REGION=${REGION:-asia-south1}
url() { gcloud run services describe "$1" --region "$REGION" --format 'value(status.url)'; }

cd "$(dirname "$0")/.."
[ -f infra/deploy.env ] && . infra/deploy.env
SITE=${FIREBASE_SITE:-$GOOGLE_CLOUD_PROJECT}
VITE_VENDOR_URL=$(url vendor-svc)
VITE_RETAILER_URL=$(url retailer-svc)
VITE_GATEWAY_URL=$(url trust-gateway)
for u in "$VITE_VENDOR_URL" "$VITE_RETAILER_URL" "$VITE_GATEWAY_URL"; do
  [ -n "$u" ] || { echo "a service has no URL yet: run infra/deploy.sh first" >&2; exit 1; }
done
export VITE_VENDOR_URL VITE_RETAILER_URL VITE_GATEWAY_URL VITE_HOSTED=true
sites=$(firebase hosting:sites:list --project "$GOOGLE_CLOUD_PROJECT" 2>/dev/null || true)
grep -qw "$SITE" <<<"$sites" || {
  if [ "$SITE" = "$GOOGLE_CLOUD_PROJECT" ]; then hint="firebase projects:addfirebase $SITE"
  else hint="firebase hosting:sites:create $SITE --project $GOOGLE_CLOUD_PROJECT"; fi
  echo "no Firebase Hosting site $SITE: $hint" >&2; exit 1; }
(cd web && npm ci && npm run build)
# firebase.json deploys the "web" target; point it at this site (.firebaserc, not committed).
firebase target:clear hosting web --project "$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1 || true
firebase target:apply hosting web "$SITE" --project "$GOOGLE_CLOUD_PROJECT"
firebase deploy --only hosting:web --project "$GOOGLE_CLOUD_PROJECT"
echo "web: https://${SITE}.web.app"
