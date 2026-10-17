import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { c } from "./lib/i18n";
import "@fontsource-variable/inter";
import "./styles.css";
import { Icon, Logo } from "./lib/icons";

/** The landing page: links to the two separate apps (plain links, so each loads its own bundle). */
function Home() {
  return (
    <main className="home">
      <h1>
        <Logo size={44} /> VendorLink
      </h1>
      <p>{c("home.tagline")}</p>
      <div className="home-links">
        <a href="/vendor"><Icon name="phone" size={22} /> {c("home.vendor_app")}</a>
        <a href="/retailer"><Icon name="monitor" size={22} /> {c("home.retailer_console")}</a>
      </div>
      <p className="muted">{c("simulated")}</p>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Home />
  </StrictMode>,
);
