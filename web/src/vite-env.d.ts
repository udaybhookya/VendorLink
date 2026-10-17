/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_VENDOR_URL?: string;
  readonly VITE_RETAILER_URL?: string;
  readonly VITE_GATEWAY_URL?: string;
  readonly VITE_HOSTED?: string; // "true" in infra/deploy_web.sh: no dev-only pages
}
