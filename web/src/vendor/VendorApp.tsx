import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { Vendor, VendorOptions } from "../types/generated";
import { api, coalesce, subscribe, VENDOR_URL } from "../lib/api";
import { languageName, strings } from "../lib/i18n";
import { useWide } from "../lib/useWide";
import { Icon, type IconName, Logo } from "../lib/icons";
import { Talk } from "./Talk";
import { Deals } from "./Deals";
import { Activity } from "./Activity";
import { Products } from "./Products";
import { Setup } from "./Setup";
import { NeedsYou, useInbox } from "./NeedsYou";

type Tab = "talk" | "deals" | "products" | "setup" | "activity";
const TAB_ICONS: Record<Tab, IconName> = { talk: "chat", deals: "deals", products: "products", setup: "setup", activity: "activity" };
const TABS: Tab[] = ["talk", "deals", "products", "setup", "activity"];

export function VendorApp() {
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [vendorId, setVendorId] = useState(() => {
    try {
      return params.get("vendor") ?? localStorage.getItem("vl.vendor") ?? "";
    } catch {
      return params.get("vendor") ?? "";
    }
  });
  const [tab, setTab] = useState<Tab>(() => (TABS.includes(params.get("tab") as Tab) ? (params.get("tab") as Tab) : "talk"));
  const [tick, setTick] = useState(0);
  /** Bumped by "Add a product" anywhere: the chat opens and asks for the photo. */
  const [startListing, setStartListing] = useState(0);
  const wide = useWide();
  const [error, setError] = useState("");

  const vendor = vendors.find((v) => v.id === vendorId);
  // Languages each country offers (from the Country Packs); English is always among them.
  const [languages, setLanguages] = useState<Record<string, string[]>>({});
  useEffect(() => {
    api<VendorOptions>(VENDOR_URL, "/options")
      .then((o) => setLanguages(Object.fromEntries(o.countries.map((c) => [c.code, c.languages]))))
      .catch(() => setLanguages({}));
  }, []);

  /** Her preferred language: the app, chat, read-backs and the agents' questions all follow it. */
  async function changeLanguage(language: string) {
    try {
      await api(VENDOR_URL, `/vendors/${vendorId}/profile`, { method: "PATCH", json: { language } });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }
  const t = useMemo(() => strings(vendor?.language ?? "en"), [vendor?.language]);
  const refresh = useMemo(() => coalesce(() => setTick((n) => n + 1)), []);
  const inbox = useInbox(vendor, tick);
  const addProduct = () => {
    setTab("talk");
    setStartListing((n) => n + 1);
  };

  useEffect(() => {
    api<Vendor[]>(VENDOR_URL, "/vendors").then(setVendors).catch((e) => setError(String(e)));
  }, [tick]);

  // A remembered or linked vendor that no longer exists (renamed, reset): show a real one instead.
  useEffect(() => {
    if (vendors.length && !vendors.some((v) => v.id === vendorId)) setVendorId(vendors[0].id);
  }, [vendors, vendorId]);

  useEffect(() => {
    if (!vendorId) return; // chosen once the vendor list loads
    try {
      localStorage.setItem("vl.vendor", vendorId);
    } catch {
      /* storage may be blocked; the picker still works */
    }
    return subscribe(`${VENDOR_URL}/vendors/${vendorId}/events`, refresh);
  }, [vendorId, refresh]);

  const languagePicker = vendor && (
    <label className="with-icon lang">
    <Icon name="globe" size={16} />
    <select
      aria-label={t("ui.language")}
      title={t("ui.language")}
      value={vendor.language}
      onChange={(e) => void changeLanguage(e.target.value)}
    >
      {(languages[vendor.country] ?? [vendor.language]).map((l) => (
        <option key={l} value={l}>{languageName(l, true)}</option>
      ))}
    </select>
    </label>
  );
  const vendorPicker = (
    <select aria-label="vendor" value={vendorId} onChange={(e) => (e.target.value === "__new" ? navigate("/vendor/join") : setVendorId(e.target.value))}>
      {vendors.map((v) => (
        <option key={v.id} value={v.id}>
          {v.display_name} · {v.country}
        </option>
      ))}
      <option value="__new">{t("ui.new_supplier")}</option>
    </select>
  );
  const pages: Record<Exclude<Tab, "talk">, JSX.Element | null> = vendor
    ? {
        deals: <Deals vendor={vendor} t={t} tick={tick} onChange={refresh} />,
        products: <Products vendor={vendor} t={t} tick={tick} onChange={refresh} onAdd={addProduct} />,
        setup: <Setup vendor={vendor} t={t} tick={tick} onChange={refresh} />,
        activity: <Activity vendor={vendor} t={t} tick={tick} />,
      }
    : { deals: null, products: null, setup: null, activity: null };
  // The chat stays mounted on every tab, so the conversation is still there when she comes back.
  const talk = vendor && <Talk vendor={vendor} t={t} onChange={refresh} startListing={startListing} />;
  const needs = vendor && <NeedsYou vendor={vendor} t={t} tick={tick} inbox={inbox} onChange={refresh} />;
  const badge = (k: Tab) => k === "talk" && inbox.count > 0 && <span className="badge" aria-label={t("ui.needs.title")}>{inbox.count}</span>;

  if (wide) {
    return (
      <div className="vapp">
        <header className="vapp-head">
          <div className="brand"><Logo size={30} /> VendorLink</div>
          {vendor && <span className="vapp-who">{vendor.display_name} · {vendor.country}</span>}
          <span className="demo-tag" title={t("ui.simulated")}>{t("ui.demo_tag")}</span>
          <span className="spacer" />
          {languagePicker}
          {vendorPicker}
        </header>
        {error && <div className="error">{error}</div>}
        <div className="vapp-main">
          <nav className="vapp-nav">
            {TABS.map((k) => (
              <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
                <Icon name={TAB_ICONS[k]} size={19} /> {t(`ui.tabs.${k}`)} {badge(k)}
              </button>
            ))}
          </nav>
          <div className="vapp-home" hidden={tab !== "talk"}>
            <section className="vapp-chat" aria-label={t("ui.assistant")}>{talk}</section>
            <aside className="needs-rail" aria-label={t("ui.needs.title")}>
              <h2>{t("ui.needs.title")}{inbox.count > 0 && <span className="badge">{inbox.count}</span>}</h2>
              {needs}
            </aside>
          </div>
          {tab !== "talk" && (
            <main className="vapp-page">
              <h1>{t(`ui.tabs.${tab}`)}</h1>
              {pages[tab]}
            </main>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="phone">
      <header className="phone-head">
        <div className="brand"><Logo size={28} /> <span>VendorLink</span></div>
        <span className="demo-tag" title={t("ui.simulated")}>{t("ui.demo_tag")}</span>
        {languagePicker}
        {vendorPicker}
      </header>
      {error && <div className="error">{error}</div>}
      <main className="phone-body">
        <div className="phone-home" hidden={tab !== "talk"}>
          {inbox.count > 0 && (
            <details className="needs-strip">
              <summary><span className="badge">{inbox.count}</span> {t("ui.needs.title")}</summary>
              {needs}
            </details>
          )}
          {talk}
        </div>
        {tab !== "talk" && pages[tab]}
      </main>
      <nav className="tabbar">
        {TABS.map((k) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
            <span className="tab-icon"><Icon name={TAB_ICONS[k]} size={22} />{badge(k)}</span>
            {t(`ui.tabs.${k}`)}
          </button>
        ))}
      </nav>
    </div>
  );
}
