import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { AgentCredential, Vendor, VendorOptions } from "../types/generated";
import { api, GATEWAY_URL, VENDOR_URL } from "../lib/api";
import { languageName, strings } from "../lib/i18n";
import { Icon } from "../lib/icons";

/** Supplier self-registration: creates the vendor and her own agent at the gateway. */
export function Join() {
  const navigate = useNavigate();
  const [options, setOptions] = useState<VendorOptions | null>(null);
  // English by default; she can pick her own language below (or later, in the app header).
  const [picked, setForm] = useState({ display_name: "", legal_name: "", country: "", language: "en", business_profile: "" });
  const form = {
    ...picked,
    country: picked.country || options?.countries[0]?.code || "",
    business_profile: picked.business_profile || options?.business_profiles[0]?.id || "",
  };
  const [created, setCreated] = useState<{ vendor: Vendor; agent: AgentCredential } | null>(null);
  const [error, setError] = useState("");
  const t = useMemo(() => strings(form.language), [form.language]);

  useEffect(() => {
    api<VendorOptions>(VENDOR_URL, "/options").then(setOptions).catch((e) => setError(String(e)));
  }, []);

  const country = options?.countries.find((c) => c.code === form.country);

  async function create() {
    setError("");
    try {
      const vendor = await api<Vendor>(VENDOR_URL, "/vendors", { method: "POST", json: form });
      const agent = await api<AgentCredential>(GATEWAY_URL, `/agents/${vendor.agent_id}`);
      setCreated({ vendor, agent });
      try {
        localStorage.setItem("vl.vendor", vendor.id);
      } catch {
        /* storage blocked: the picker still lists her */
      }
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="phone">
      <header className="phone-head">
        <div className="brand">VendorLink</div>
      </header>
      <div className="sim-banner">{t("ui.simulated")}</div>
      <main className="phone-body">
        <h2>{t("ui.register.title")}</h2>
        {error && <div className="error">{error}</div>}
        {!created ? (
          <form className="stack" onSubmit={(e) => { e.preventDefault(); void create(); }}>
            <p className="muted">{t("ui.register.intro")}</p>
            <label>{t("ui.register.display_name")}
              <input required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </label>
            <label>{t("ui.register.legal_name")}
              <input required value={form.legal_name} onChange={(e) => setForm({ ...form, legal_name: e.target.value })} />
            </label>
            <label>{t("ui.register.country")}
              <select value={form.country} onChange={(e) => {
                const c = options?.countries.find((x) => x.code === e.target.value);
                const keep = c?.languages.includes(form.language);
                setForm({ ...form, country: e.target.value, language: keep ? form.language : (c?.default_language ?? "en") });
              }}>
                {options?.countries.map((c) => <option key={c.code} value={c.code}>{c.name.en ?? c.code}</option>)}
              </select>
            </label>
            <label>{t("ui.register.language")}
              <select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
                {country?.languages.map((l) => <option key={l} value={l}>{languageName(l)}</option>)}
              </select>
            </label>
            <label>{t("ui.register.business")}
              <select value={form.business_profile} onChange={(e) => setForm({ ...form, business_profile: e.target.value })}>
                {options?.business_profiles.map((b) => <option key={b.id} value={b.id}>{b.name[form.language] ?? b.name.en ?? b.id}</option>)}
              </select>
            </label>
            <button type="submit" className="big"><Icon name="bot" /> {t("ui.register.create")}</button>
          </form>
        ) : (
          <section className="stack">
            <div className="agent-card">
              <div className="agent-title"><Icon name="check" /> {t("ui.register.agent_ready")}</div>
              <div>{t("ui.register.agent_id")}: <code>{created.agent.agent_id}</code></div>
              <div>{t("ui.register.fingerprint")}: <code>{created.agent.key_fingerprint}</code></div>
              <div>{t("ui.register.status")}: <span className="pill active">{created.agent.status}</span></div>
            </div>
            <button className="big" onClick={() => navigate(`/vendor?vendor=${encodeURIComponent(created.vendor.id)}&tab=setup`)}>
              {t("ui.register.start")} →
            </button>
          </section>
        )}
      </main>
    </div>
  );
}
