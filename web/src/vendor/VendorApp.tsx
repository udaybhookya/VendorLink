import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { Vendor, VendorOptions } from "../types/generated";
import { api, subscribe, VENDOR_URL } from "../lib/api";
import { languageName, strings } from "../lib/i18n";
import { useWide } from "../lib/useWide";
import { Icon, type IconName, Logo } from "../lib/icons";
import { Talk } from "./Talk";
import { Deals } from "./Deals";
import { Activity } from "./Activity";
import { Products } from "./Products";
import { Setup } from "./Setup";

type Tab = "talk" | "deals" | "products" | "setup" | "activity";
const TAB_ICONS: Record<Tab, IconName> = { talk: "chat", deals: "deals", products: "products", setup: "setup", activity: "activity" };
/** On a laptop the assistant is always open beside the page, so "talk" is not a page there. */
const PAGES: Exclude<Tab, "talk">[] = ["deals", "products", "setup", "activity"];

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
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) ?? "talk");
  const [tick, setTick] = useState(0);
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
  const refresh = useCallback(() => setTick((n) => n + 1), []);

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
  const pages: Record<Tab, JSX.Element | null> = vendor
    ? {
        talk: <Talk vendor={vendor} t={t} onChange={refresh} />,
        deals: <Deals vendor={vendor} t={t} tick={tick} onChange={refresh} />,
        products: <Products vendor={vendor} t={t} tick={tick} onChange={refresh} />,
        setup: <Setup vendor={vendor} t={t} tick={tick} onChange={refresh} />,
        activity: <Activity vendor={vendor} t={t} tick={tick} />,
      }
    : { talk: null, deals: null, products: null, setup: null, activity: null };

  if (wide) {
    const page = tab === "talk" ? "deals" : tab;
    return (
      <div className="vapp">
        <header className="vapp-head">
          <div className="brand"><Logo size={30} /> VendorLink</div>
          {vendor && <span className="vapp-who">{vendor.display_name} · {vendor.country}</span>}
          <span className="spacer" />
          {languagePicker}
          {vendorPicker}
        </header>
        <div className="sim-banner">{t("ui.simulated")}</div>
        {error && <div className="error">{error}</div>}
        <div className="vapp-main">
          <nav className="vapp-nav">
            {PAGES.map((k) => (
              <button key={k} className={page === k ? "active" : ""} onClick={() => setTab(k)}>
                <Icon name={TAB_ICONS[k]} size={19} /> {t(`ui.tabs.${k}`)}
              </button>
            ))}
          </nav>
          <main className="vapp-page">
            <h1>{t(`ui.tabs.${page}`)}</h1>
            {pages[page]}
          </main>
          <aside className="vapp-chat" aria-label={t("ui.assistant")}>
            <div className="vapp-chat-head"><Icon name="chat" /> {t("ui.assistant")}</div>
            {pages.talk}
          </aside>
        </div>
      </div>
    );
  }

  return (
    <div className="phone">
      <header className="phone-head">
        <div className="brand"><Logo size={28} /> <span>VendorLink</span></div>
        {languagePicker}
        {vendorPicker}
      </header>
      <div className="sim-banner">{t("ui.simulated")}</div>
      {error && <div className="error">{error}</div>}
      <main className="phone-body">{pages[tab]}</main>
      <nav className="tabbar">
        {(Object.keys(TAB_ICONS) as Tab[]).map((k) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
            <Icon name={TAB_ICONS[k]} size={22} />
            {t(`ui.tabs.${k}`)}
          </button>
        ))}
      </nav>
    </div>
  );
}
