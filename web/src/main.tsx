import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { VendorApp } from "./vendor/VendorApp";
import { Join } from "./vendor/Join";
import { Console } from "./retailer/Console";
import { c } from "./lib/i18n";
import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-telugu";
import "./styles.css";
import { Icon, Logo } from "./lib/icons";

function Home() {
  return (
    <main className="home">
      <h1>
        <Logo size={44} /> VendorLink
      </h1>
      <p>{c("home.tagline")}</p>
      <div className="home-links">
        <Link to="/vendor"><Icon name="phone" size={22} /> {c("home.vendor_app")}</Link>
        <Link to="/retailer"><Icon name="monitor" size={22} /> {c("home.retailer_console")}</Link>
      </div>
      <p className="muted">{c("simulated")}</p>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/vendor" element={<VendorApp />} />
        <Route path="/vendor/join" element={<Join />} />
        <Route path="/retailer/*" element={<Console />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
