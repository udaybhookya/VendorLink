import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Console } from "./Console";
import "@fontsource-variable/inter";
import "../styles.css";

/** The retailer console: its own page (retailer/index.html), bundle and manifest, installable on its own. */
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/retailer/*" element={<Console />} />
        <Route path="*" element={<Navigate to="/retailer" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
