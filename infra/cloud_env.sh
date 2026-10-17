# Point the local demo scripts at the hosted demo. Source it (bash or zsh), do not run it:
#   source infra/cloud_env.sh && uv run python scripts/demo_reset.py --scene waiting
# Exports the service URLs from Cloud Run and the admin token from Secret Manager (ADR 0014).
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
_vl_region=${REGION:-asia-south1}
_vl_url() { gcloud run services describe "$1" --region "$_vl_region" --project "$GOOGLE_CLOUD_PROJECT" --format 'value(status.url)'; }
export TRUST_GATEWAY_URL="$(_vl_url trust-gateway)"
export VENDOR_SVC_URL="$(_vl_url vendor-svc)"
export RETAILER_SVC_URL="$(_vl_url retailer-svc)"
export MOCKS_URL="$(_vl_url mocks)"
export VL_ADMIN_TOKEN="$(gcloud secrets versions access latest --secret vl-admin-token --project "$GOOGLE_CLOUD_PROJECT")"
# The scripts read .env too; these keep them off the local stack's dev-only paths.
export VL_DEV_ENDPOINTS=false VL_CHANNELS=mcp
unset -f _vl_url; unset _vl_region
echo "demo scripts now target: gateway=$TRUST_GATEWAY_URL vendor=$VENDOR_SVC_URL retailer=$RETAILER_SVC_URL"
