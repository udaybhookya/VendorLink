import { useEffect, useState } from "react";
import type { HumanTask, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { speak } from "../lib/speech";
import { Icon } from "../lib/icons";

/** Questions agents are asking her. Answers resume the waiting work. Consent stays on the deal. */
export function Questions({ vendor, t, tick, onChange, skipConsent = false }: {
  vendor: Vendor; t: T; tick: number; onChange: () => void; skipConsent?: boolean;
}) {
  const [tasks, setTasks] = useState<HumanTask[]>([]);
  const [text, setText] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    api<HumanTask[]>(VENDOR_URL, `/vendors/${vendor.id}/questions`).then(setTasks).catch(() => setTasks([]));
  }, [vendor.id, tick]);

  async function answer(task: HumanTask, value: string) {
    setError("");
    try {
      await api(VENDOR_URL, `/vendors/${vendor.id}/questions/${task.id}/answer`, { method: "POST", json: { text: value } });
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  // a listing to check is shown with its photos (Chat → Needs you, Products), not as a text question
  // (`skipConsent`: the deal card waiting for her yes is shown right beside this list)
  const open = tasks.filter((q) => q.kind !== "notice" && q.request.field !== "listing_review" && !(skipConsent && q.kind === "consent"));
  if (!open.length) return null;
  return (
    <section className="questions">
      <h3><Icon name="question" /> {t("ui.questions")} ({open.length})</h3>
      {error && <div className="error">{error}</div>}
      {open.map((q) => (
        <article key={q.id} className="question">
          <p onClick={() => speak(q.question, vendor.language)}><Icon name="speaker" /> <span>{q.question}</span></p>
          {q.kind === "consent" ? (
            <p className="muted small">{t("ui.tabs.deals")} ↓</p>
          ) : (q.request.choices ?? []).length ? (
            <div className="actions">
              {(q.request.choices ?? []).map((c) => (
                <button key={c.id} onClick={() => void answer(q, c.id)}>{c.label}</button>
              ))}
            </div>
          ) : (
            <form className="composer" onSubmit={(e) => { e.preventDefault(); void answer(q, text[q.id] ?? ""); }}>
              <input lang={vendor.language} inputMode={q.request.answer_type === "number" ? "decimal" : "text"}
                value={text[q.id] ?? ""} onChange={(e) => setText({ ...text, [q.id]: e.target.value })} />
              <button type="submit" disabled={!text[q.id]}>{t("ui.answer")}</button>
            </form>
          )}
        </article>
      ))}
    </section>
  );
}
