import { resolve } from "node:path";
import { defineConfig, type Connect, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/** The vendor app and the retailer console are two separate pages, each with its own bundle and
 * manifest. Client-side routes under /vendor and /retailer fall back to that app's own index.html. */
const APPS = ["vendor", "retailer"] as const;

const appFallback: Connect.NextHandleFunction = (req, _res, next) => {
  const path = (req.url ?? "").split(/[?#]/)[0];
  const app = APPS.find((a) => path === `/${a}` || path.startsWith(`/${a}/`));
  // Only page routes; files (index.html itself, assets) are served as they are.
  if (app && !path.split("/").pop()!.includes(".")) req.url = `/${app}/index.html`;
  next();
};

const twoApps: Plugin = {
  name: "vendorlink-two-apps",
  configureServer: (server) => void server.middlewares.use(appFallback),
  configurePreviewServer: (server) => void server.middlewares.use(appFallback),
};

export default defineConfig({
  plugins: [react(), twoApps],
  server: { port: 5173, host: true },
  build: {
    rollupOptions: {
      input: {
        home: resolve(__dirname, "index.html"),
        vendor: resolve(__dirname, "vendor/index.html"),
        retailer: resolve(__dirname, "retailer/index.html"),
      },
    },
  },
});
