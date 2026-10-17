import { useEffect, useState } from "react";
import type { DirectoryEntry, DocSpec, RequiredDocs, RulesView, Vendor, VendorCheckOut, VendorInvite } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { downscale } from "../lib/image";
import { speak } from "../lib/speech";
import { Rules } from "./Rules";
import { Icon } from "../lib/icons";

type SkuRow = { sku: string; unit: string; price_floor: string; max_qty_per_day: string };

/** Onboarding checklist: documents → selling rules (read back, then signed) → retailers. */
export function Setup({ vendor, t, tick, onChange }: { vendor: Vendor; t: T; tick: number; onChange: () => void }) {
  const [docs, setDocs] = useState<RequiredDocs | null>(null);
  const [rules, setRules] = useState<RulesView | null>(null);
  const [retailers, setRetailers] = useState<DirectoryEntry[]>([]);
  const [invites, setInvites] = useState<VendorInvite[]>([]);
  const [checks, setChecks] = useState<VendorCheckOut | null>(null);
  const [editRules, setEditRules] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const id = vendor.id;
    api<RequiredDocs>(VENDOR_URL, `/vendors/${id}/required-docs`).then(setDocs).catch(() => setDocs(null));
    api<RulesView>(VENDOR_URL, `/vendors/${id}/rules`).then(setRules).catch(() => setRules(null));
    api<DirectoryEntry[]>(VENDOR_URL, "/directory/retailers").then(setRetailers).catch(() => setRetailers([]));
    api<VendorInvite[]>(VENDOR_URL, `/vendors/${id}/invites`).then(setInvites).catch(() => setInvites([]));
  }, [vendor.id, tick]);

  const guard = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      onChange();
    } catch (e) {
      setError(String(e));
    }
  };

  // where she is: documents in, rules signed, at least one retailer
  const have = new Map((vendor.docs ?? []).map((d) => [d.doc_type, d.status]));
  const steps = [
    { key: "documents", done: Boolean(docs) && Object.values(docs?.roles ?? {}).every((specs) =>
        (specs as DocSpec[]).some((spec) => have.has(spec.id) && have.get(spec.id) !== "failed")) },
    { key: "rules", done: Boolean(rules?.mandate?.signature) },
    { key: "retailers", done: (vendor.retailers ?? []).length > 0 },
  ];

  return (
    <section className="stack setup-page">
      <ol className="steps-bar">
        {steps.map((s, i) => (
          <li key={s.key} className={s.done ? "done" : ""}>
            <span className="step-mark">{s.done ? <Icon name="check" size={14} /> : i + 1}</span>
            {t(`ui.setup.step_${s.key}`)}
          </li>
        ))}
      </ol>
      {error && <div className="error">{error}</div>}
      <Documents vendor={vendor} t={t} docs={docs} save={(body) => guard(() => api(VENDOR_URL, `/vendors/${vendor.id}/documents`, { method: "POST", json: body }))} />

      <div className="card-plain">
        <h3>{t("ui.setup.rules_title")}</h3>
        {rules?.mandate && <Rules vendor={vendor} t={t} tick={tick} />}
        {rules?.mandate && !editRules ? (
          <button className="secondary" onClick={() => setEditRules(true)}>{t("ui.setup.change_rules")}</button>
        ) : (
          <RulesForm vendor={vendor} t={t} retailers={retailers} rules={rules} setError={setError}
            onChange={() => { setEditRules(false); onChange(); }} />
        )}
      </div>

      <div className="card-plain">
        <h3>{t("ui.setup.retailers")}</h3>
        {invites.map((i) => (
          <p key={i.invite_id} className="invite"><Icon name="mail" size={16} /> <b>{i.retailer_name || i.retailer_id}</b> {t("ui.setup.invited_by")}{i.message && `: “${i.message}”`}</p>
        ))}
        {retailers.map((r) => {
          const applied = vendor.retailers?.includes(r.party_id);
          return (
            <div key={r.party_id} className="row">
              <span className="row-label"><Icon name="store" /> {r.display_name}</span>
              <button className={applied ? "secondary" : ""} onClick={() => void guard(async () => {
                setChecks(await api<VendorCheckOut>(VENDOR_URL, `/vendors/${vendor.id}/apply?retailer_id=${encodeURIComponent(r.party_id)}`, { method: "POST" }));
              })}>
                {applied ? t("ui.setup.applied") : t("ui.setup.apply")}
              </button>
            </div>
          );
        })}
        <p className="muted">{t("ui.setup.status")}: <span className="pill">{vendor.status}</span></p>
        {checks && (
          <ul className="checks">
            {(checks.checks ?? []).map((c, i) => (
              <li key={i} className={c.status}><Icon name={c.status === "pass" ? "check" : "x"} size={15} /> {c.doc_type}: {c.code}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Documents({ vendor, t, docs, save }: {
  vendor: Vendor; t: T; docs: RequiredDocs | null;
  save: (body: { doc_type: string; number: string; data_b64?: string; content_type?: string; filename?: string }) => Promise<void>;
}) {
  const [numbers, setNumbers] = useState<Record<string, string>>({});
  const [photos, setPhotos] = useState<Record<string, { data_b64: string; content_type: string }>>({});
  const have = Object.fromEntries((vendor.docs ?? []).map((d) => [d.doc_type, d]));
  return (
    <div className="card-plain">
      <h3>{t("ui.setup.documents")}</h3>
      {docs && Object.entries(docs.roles).map(([role, specs]) => (
        <div key={role} className="doc-role">
          {(specs as DocSpec[]).slice(0, 1).map((spec) => {
            const existing = have[spec.id];
            return (
              <div key={spec.id} className="stack-tight">
                <b>{spec.name[vendor.language] ?? spec.name.en ?? spec.id}</b>
                {existing && <span className={`pill ${existing.status}`}>{existing.status}{existing.failure_reason ? ` · ${existing.failure_reason}` : ""}</span>}
                <input placeholder={t("ui.setup.number")} value={numbers[spec.id] ?? existing?.number ?? ""}
                  onChange={(e) => setNumbers({ ...numbers, [spec.id]: e.target.value })} />
                <label className="small muted">{t("ui.setup.photo_optional")}
                  <input type="file" accept="image/*,application/pdf" capture="environment" onChange={async (e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    if (f.type === "application/pdf") {
                      const buf = new Uint8Array(await f.arrayBuffer());
                      let bin = "";
                      buf.forEach((b) => (bin += String.fromCharCode(b)));
                      setPhotos({ ...photos, [spec.id]: { data_b64: btoa(bin), content_type: f.type } });
                    } else setPhotos({ ...photos, [spec.id]: await downscale(f) });
                  }} />
                </label>
                <button disabled={!(numbers[spec.id] ?? existing?.number)} onClick={() => void save({
                  doc_type: spec.id, number: numbers[spec.id] ?? existing?.number ?? "", ...(photos[spec.id] ?? {}), filename: spec.id,
                })}>{t("ui.setup.save")}</button>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function RulesForm({ vendor, t, retailers, rules, onChange, setError }: {
  vendor: Vendor; t: T; retailers: DirectoryEntry[]; rules: RulesView | null; onChange: () => void; setError: (e: string) => void;
}) {
  const current = rules?.mandate;
  const [rows, setRows] = useState<SkuRow[]>(() =>
    current?.skus.map((s) => ({ sku: s.sku, unit: s.unit, price_floor: String(s.price_floor), max_qty_per_day: String(s.max_qty_per_day) })) ??
    [{ sku: "", unit: "kg", price_floor: "", max_qty_per_day: "" }]);
  const [from, setFrom] = useState(current?.pickup_windows[0]?.start.slice(0, 5) ?? "06:00");
  const [to, setTo] = useState(current?.pickup_windows[0]?.end.slice(0, 5) ?? "10:00");
  const [days, setDays] = useState(String(current?.payment_days_max ?? 7));
  const [auto, setAuto] = useState(String(current?.auto_accept.max_value ?? "5000"));
  const [buyers, setBuyers] = useState<string[]>(current?.approved_buyers ?? vendor.retailers ?? []);
  const [draft, setDraft] = useState<RulesView | null>(null);

  async function preview() {
    setError("");
    try {
      const view = await api<RulesView>(VENDOR_URL, `/vendors/${vendor.id}/rules/draft`, {
        method: "POST",
        json: {
          skus: rows.filter((r) => r.sku).map((r) => ({ ...r, sku: r.sku.trim().toLowerCase() })),
          pickup_windows: [{ start: from, end: to }], payment_days_max: Number(days),
          approved_buyers: buyers, auto_accept_max_value: auto,
        },
      });
      setDraft(view);
      speak(view.readback ?? "", vendor.language);
    } catch (e) {
      setError(String(e));
    }
  }

  async function sign() {
    if (!draft?.subject_hash) return;
    setError("");
    try {
      const word = t.words("affirmatives")[0] ?? "yes";
      await api(VENDOR_URL, `/vendors/${vendor.id}/rules/consent`, {
        method: "POST", json: { subject_hash: draft.subject_hash, transcript: `${word} (tap)`, language: vendor.language },
      });
      setDraft(null);
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="stack-tight">
      {rows.map((r, i) => (
        <div key={i} className="sku-row">
          <input placeholder={t("ui.setup.sku")} value={r.sku} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, sku: e.target.value } : x)))} />
          <input placeholder={t("ui.setup.unit")} value={r.unit} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, unit: e.target.value } : x)))} />
          <input placeholder={t("ui.min_price")} inputMode="decimal" value={r.price_floor} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, price_floor: e.target.value } : x)))} />
          <input placeholder={t("ui.setup.max_per_day")} inputMode="numeric" value={r.max_qty_per_day} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, max_qty_per_day: e.target.value } : x)))} />
        </div>
      ))}
      <button className="secondary" onClick={() => setRows([...rows, { sku: "", unit: "kg", price_floor: "", max_qty_per_day: "" }])}>{t("ui.setup.add_sku")}</button>
      <div className="row">
        <label>{t("ui.setup.pickup_from")} <input type="time" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>{t("ui.setup.pickup_to")} <input type="time" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      <label>{t("ui.setup.payment_days")} <input inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} /></label>
      <label>{t("ui.setup.auto_accept")} <input inputMode="decimal" value={auto} onChange={(e) => setAuto(e.target.value)} /></label>
      <fieldset>
        <legend>{t("ui.setup.buyers")}</legend>
        {retailers.map((r) => (
          <label key={r.party_id} className="check">
            <input type="checkbox" checked={buyers.includes(r.party_id)}
              onChange={(e) => setBuyers(e.target.checked ? [...buyers, r.party_id] : buyers.filter((b) => b !== r.party_id))} />
            {r.display_name}
          </label>
        ))}
      </fieldset>
      <button className="secondary" onClick={() => void preview()}><Icon name="speaker" /> {t("ui.setup.preview")}</button>
      {draft && (
        <div className="readback-box">
          <p>{draft.readback}</p>
          <button className="yes big" onClick={() => void sign()}><Icon name="check" /> {t("ui.setup.consent_yes")}</button>
        </div>
      )}
    </div>
  );
}
