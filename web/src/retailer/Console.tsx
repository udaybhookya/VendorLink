import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import type { ActiveConfig, Retailer } from "../types/generated";
import { api, GATEWAY_URL, RETAILER_URL, shortHash, subscribe } from "../lib/api";
import { c } from "../lib/i18n";
import { Icon, type IconName, Logo } from "../lib/icons";
import { Today } from "./Today";
import { Studio } from "./Studio";
import { Suppliers } from "./Suppliers";
import { Observability } from "./Observability";
import { Outbox } from "./Outbox";
import { Inbox, useInboxCount } from "./Inbox";
import { Orders } from "./Orders";
import { Trust } from "./Trust";
import { Catalogue } from "./Catalogue";

/** Day-to-day work first; setup and the under-the-hood pages below, under Admin. */
const NAV: [string, IconName][] = [
  ["today", "dashboard"], ["inbox", "inbox"], ["orders", "orders"], ["catalogue", "products"], ["suppliers", "users"],
];
const ADMIN: [string, IconName][] = [
  ["studio", "setup"], ["observability", "pulse"], ["trust", "shield"], ["outbox", "send"],
];

export type PageProps = { retailer: Retailer; active: ActiveConfig | null; tick: number; refresh: () => void };

export function Console() {
  const [retailer, setRetailer] = useState<Retailer | null>(null);
  const [active, setActive] = useState<ActiveConfig | null>(null);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState("");
  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    api<Retailer[]>(RETAILER_URL, "/retailers")
      .then((rs) => setRetailer(rs[0] ?? null))
      .catch((e) => setError(String(e)));
  }, [tick]);

  useEffect(() => {
    if (!retailer) return;
    api<ActiveConfig>(RETAILER_URL, `/retailers/${retailer.id}/config/active`).then(setActive).catch(() => setActive(null));
  }, [retailer, tick]);

  useEffect(() => {
    if (!retailer) return;
    const a = subscribe(`${RETAILER_URL}/retailers/${retailer.id}/events`, refresh);
    const b = subscribe(`${GATEWAY_URL}/events`, refresh);
    return () => {
      a();
      b();
    };
  }, [retailer, refresh]);

  const props = retailer ? { retailer, active, tick, refresh } : null;
  const waiting = useInboxCount(retailer, tick);
  const link = ([k, icon]: [string, IconName]) => (
    <NavLink key={k} to={`/retailer/${k}`}>
      <Icon name={icon} size={18} /> {c(`nav.${k}`)}
      {k === "inbox" && waiting > 0 && <span className="badge">{waiting}</span>}
    </NavLink>
  );
  return (
    <div className="console">
      <aside className="sidebar">
        <div className="brand"><Logo size={30} tone="console" /> VendorLink</div>
        <div className="muted small">{retailer?.display_name} <span className="demo-tag" title={c("simulated")}>{c("demo_tag")}</span></div>
        <nav>
          {NAV.map(link)}
          <div className="nav-group">{c("nav.admin")}</div>
          {ADMIN.map(link)}
        </nav>
        <div className="config-chip">
          <div className="muted small">{c("today.config")}</div>
          <strong>{active?.retailer_profile.retailer_type} · {active?.country_pack.code}</strong>
          <code>{shortHash(active?.config_hash)}</code>
        </div>
      </aside>
      <section className="content">
        {error && <div className="error">{error}</div>}
        {props && (
          <Routes>
            <Route path="today" element={<Today {...props} />} />
            <Route path="studio" element={<Studio {...props} />} />
            <Route path="suppliers" element={<Suppliers {...props} />} />
            <Route path="observability" element={<Observability {...props} />} />
            <Route path="inbox" element={<Inbox {...props} />} />
            <Route path="tasks" element={<Navigate to="../inbox" replace />} />
            <Route path="queue" element={<Navigate to="../inbox" replace />} />
            <Route path="catalogue" element={<Catalogue {...props} />} />
            <Route path="outbox" element={<Outbox {...props} />} />
            <Route path="orders" element={<Orders {...props} />} />
            <Route path="trust" element={<Trust {...props} />} />
            <Route path="*" element={<Navigate to="today" replace />} />
          </Routes>
        )}
      </section>
    </div>
  );
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[], initial: T): [T, string] {
  const [value, setValue] = useState<T>(initial);
  const [error, setError] = useState("");
  useEffect(() => {
    load()
      .then((v) => {
        setValue(v);
        setError("");
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return [value, error];
}

export function Pill({ status }: { status: string }) {
  return <span className={`pill ${status}`}>{status.replace(/_/g, " ")}</span>;
}
