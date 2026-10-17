import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { VendorApp } from "./VendorApp";
import { Join } from "./Join";
import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-telugu";
import "../styles.css";

/** The vendor app: its own page (vendor/index.html), bundle and manifest, installable on its own. */
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/vendor" element={<VendorApp />} />
        <Route path="/vendor/join" element={<Join />} />
        <Route path="*" element={<Navigate to="/vendor" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
