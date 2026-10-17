import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type {
  HumanTask, InfoResponseMessage, Listing, QueueItem, Retailer, RetailerOrder, RetailerVendor,
} from "../types/generated";
import { api, RETAILER_URL } from "../lib/api";
import { c } from "../lib/i18n";
import { Icon, type IconName } from "../lib/icons";
import { type PageProps, Pill, useLoad } from "./Console";
import { ProductDetail } from "./ProductDetail";
import { OrderReview } from "./OrderReview";
import { explain } from "./codes";

type Kind = QueueItem["kind"] | "question";
/** One thing a person must decide: a queue item (with the Slack/email task asking about it), or
 *  a question an agent or a paused job is waiting on. */
type Item = { id: string; kind: Kind; ref: string; created: string; queue?: QueueItem; task?: HumanTask };

const ICONS: Record<Kind, IconName> = {
  listing: "products", place_order: "orders", handle_counter: "deals", vendor: "users", question: "question",
};

/** Decisions waiting on people, built from the approval queue and the open tasks. */
function decisions(queue: QueueItem[], tasks: HumanTask[]): Item[] {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const fromQueue: Item[] = queue.filter((q) => q.status === "pending").map((q) => ({
    id: q.id, kind: q.kind, ref: q.ref_id, created: q.created_at, queue: q, task: q.task_id ? byId.get(q.task_id) : undefined,
  }));
  const linked = new Set(fromQueue.map((i) => i.task?.id));
  const questions: Item[] = tasks
    .filter((t) => t.status === "open" && t.kind !== "notice" && !t.resume.startsWith("queue:") && !linked.has(t.id))
    .map((t) => ({ id: t.id, kind: "question", ref: t.subject_ref, created: t.created_at, task: t }));
  return [...fromQueue, ...questions].sort((a, b) => a.created.localeCompare(b.created));
}

/** The number on the Inbox badge. */
export function useInboxCount(retailer: Retailer | null, tick: number): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!retailer) return;
    const base = `/retailers/${retailer.id}`;
    Promise.all([api<QueueItem[]>(RETAILER_URL, `${base}/queue`), api<HumanTask[]>(RETAILER_URL, `${base}/tasks`)])
      .then(([q, t]) => setCount(decisions(q, t).length))
      .catch(() => setCount(0));
  }, [retailer, tick]);
  return count;
}

const ago = (iso: string) => {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return c("inbox.ago", { t: m < 60 ? `${m}m` : m < 1440 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d` });
};

export function Inbox({ retailer, active, tick, refresh }: PageProps) {
  const base = `/retailers/${retailer.id}`;
  const [queue] = useLoad(() => api<QueueItem[]>(RETAILER_URL, `${base}/queue`), [tick], []);
  const [tasks] = useLoad(() => api<HumanTask[]>(RETAILER_URL, `${base}/tasks`), [tick], []);
  const [listings] = useLoad(() => api<Listing[]>(RETAILER_URL, `${base}/listings`), [tick], []);
  const [orders] = useLoad(() => api<RetailerOrder[]>(RETAILER_URL, `${base}/orders`), [tick], []);
  const [vendors] = useLoad(() => api<RetailerVendor[]>(RETAILER_URL, `${base}/vendors`), [tick], []);
  const [answers] = useLoad(() => api<InfoResponseMessage[]>(RETAILER_URL, `${base}/info-responses`), [tick], []);
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<"open" | "done">("open");
  const [error, setError] = useState("");
  const [text, setText] = useState("");

  const items = decisions(queue, tasks);
  const selected = items.find((i) => i.id === params.get("item")) ?? items[0];
  const vendorName = (id: string) => vendors.find((v) => v.vendor_id === id)?.display_name ?? id;
  const storeName = (id: string) => retailer.stores?.find((s) => s.id === id)?.name ?? id;

  /** What the row says: the product, order or supplier by name, not by id. */
  function describe(item: Item): { title: string; sub: string } {
    if (item.kind === "listing") {
      const l = listings.find((x) => x.id === item.ref);
      return { title: l?.title || l?.sku_name || item.ref, sub: l ? `${vendorName(l.vendor_id)} · ${l.currency} ${l.price}/${l.unit}` : "" };
    }
    if (item.kind === "place_order" || item.kind === "handle_counter") {
      const o = orders.find((x) => x.order_id === item.ref);
      const v = o?.versions.at(-1);
      return {
        title: o ? `${vendorName(o.vendor_id)} → ${storeName(o.store_id)}` : item.ref,
        sub: v ? v.lines.map((l) => `${l.qty} ${l.unit} ${l.sku}`).join(", ") : "",
      };
    }
    if (item.kind === "vendor") {
      const v = vendors.find((x) => x.vendor_id === item.ref);
      return { title: v?.display_name ?? item.ref, sub: v ? `${v.country} · ${v.business_profile.replace(/_/g, " ")}` : "" };
    }
    const p = item.task?.request.params ?? {};
    if (p.vendor && p.store) return { title: `${p.vendor} → ${p.store}`, sub: [p.value, p.order].filter(Boolean).join(" · ") };
    return { title: item.task?.question ?? item.ref, sub: item.task?.role ?? "" };
  }

  async function decide(item: Item, run: () => Promise<unknown>) {
    setError("");
    try {
      await run();
      setText("");
      // straight on to the next decision
      const next = items[items.indexOf(item) + 1] ?? items[items.indexOf(item) - 1];
      setParams(next ? { item: next.id } : {});
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }
  const resolve = (item: Item, approve: boolean) =>
    decide(item, () => api(RETAILER_URL, `${base}/queue/${item.id}/${approve ? "approve" : "reject"}`, { method: "POST" }));
  const answer = (item: Item, value: string) =>
    decide(item, () => api(RETAILER_URL, `${base}/tasks/${item.task?.id}/answer`, { method: "POST", json: { text: value, by: "console" } }));

  const done = queue.filter((q) => q.status !== "pending").slice(0, 30);
  const answered = tasks.filter((t) => t.status !== "open" && t.kind !== "notice" && !t.resume.startsWith("queue:")).slice(0, 20);

  return (
    <div className="page inbox-page">
      <h1>{c("nav.inbox")}</h1>
      <p className="muted">{c("inbox.subtitle")}</p>
      <div className="subtabs">
        <button className={view === "open" ? "active" : ""} onClick={() => setView("open")}>{c("inbox.to_decide")} ({items.length})</button>
        <button className={view === "done" ? "active" : ""} onClick={() => setView("done")}>{c("inbox.done")}</button>
      </div>
      {error && <div className="error">{error}</div>}

      {view === "open" && !items.length && <p className="empty">{c("inbox.none")}</p>}
      {view === "open" && items.length > 0 && (
        <div className="inbox">
          <ul className="inbox-list">
            {items.map((item) => {
              const d = describe(item);
              return (
                <li key={item.id}>
                  <button className={selected?.id === item.id ? "on" : ""} onClick={() => setParams({ item: item.id })}>
                    <span className="inbox-icon"><Icon name={ICONS[item.kind]} size={18} /></span>
                    <span className="inbox-text">
                      <span className="inbox-kind">{kindLabel(item)} · {ago(item.created)}</span>
                      <strong>{d.title}</strong>
                      {d.sub && <span className="muted small">{d.sub}</span>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {selected ? (
            <section className="inbox-detail" key={selected.id}>
              <header className="decision-bar">
                <div>
                  <span className="inbox-kind">{kindLabel(selected)}</span>
                  <h2>{describe(selected).title}</h2>
                </div>
                {selected.queue ? (
                  <div className="actions">
                    <button className="yes" onClick={() => void resolve(selected, true)}>
                      <Icon name="check" /> {c(`inbox.approve_${selected.kind}`)}
                    </button>
                    <button className="no" onClick={() => void resolve(selected, false)}>
                      <Icon name="x" /> {c(`inbox.reject_${selected.kind}`)}
                    </button>
                  </div>
                ) : (selected.task?.request.choices ?? []).length ? (
                  <div className="actions">
                    {(selected.task?.request.choices ?? []).map((ch) => (
                      <button key={ch.id} className={ch.id === "approve" ? "yes" : ch.id === "reject" ? "no" : ""} onClick={() => void answer(selected, ch.id)}>
                        {ch.id === "approve" && <Icon name="check" />}{ch.id === "reject" && <Icon name="x" />} {ch.label.replace(/^./, (x) => x.toUpperCase())}
                      </button>
                    ))}
                  </div>
                ) : (
                  <form className="form-inline" onSubmit={(e) => { e.preventDefault(); void answer(selected, text); }}>
                    <input value={text} onChange={(e) => setText(e.target.value)} aria-label={c("inbox.answer")} />
                    <button type="submit" disabled={!text}>{c("inbox.answer")}</button>
                  </form>
                )}
              </header>
              <Why item={selected} />
              {selected.kind === "listing" && <ProductDetail retailerId={retailer.id} listingId={selected.ref} tick={tick} compact />}
              {(selected.kind === "place_order" || selected.kind === "handle_counter") && (() => {
                const o = orders.find((x) => x.order_id === selected.ref);
                return o ? <OrderReview order={o} retailer={retailer} vendors={vendors} tz={active?.country_pack.timezone} /> : null;
              })()}
              {selected.kind === "vendor" && <SupplierChecks vendor={vendors.find((v) => v.vendor_id === selected.ref)} />}
              {selected.kind === "question" && <TaskFacts task={selected.task} />}
            </section>
          ) : (
            <p className="empty">{c("inbox.pick")}</p>
          )}
        </div>
      )}

      {view === "done" && (
        <>
          <table>
            <tbody>
              {done.map((q) => {
                const d = describe({ id: q.id, kind: q.kind, ref: q.ref_id, created: q.created_at, queue: q });
                return (
                  <tr key={q.id}>
                    <td><Pill status={q.status ?? "pending"} /></td>
                    <td className="small muted">{c(`inbox.kind_${q.kind}`)}</td>
                    <td><strong>{d.title}</strong> <span className="muted small">{d.sub}</span></td>
                    <td className="small muted">{new Date(q.created_at).toLocaleString()}</td>
                    <td>{q.kind === "listing" && <Link className="small" to={`/retailer/catalogue?listing=${encodeURIComponent(q.ref_id)}`}>{c("inbox.open_catalogue")}</Link>}</td>
                  </tr>
                );
              })}
              {answered.map((t) => (
                <tr key={t.id}>
                  <td><Pill status={t.status ?? "answered"} /></td>
                  <td className="small muted">{t.role}</td>
                  <td className="small">{t.question}</td>
                  <td><strong>{String(t.answer_value ?? t.answer ?? "")}</strong></td>
                  <td className="small muted">{t.answered_via ? `${c("tasks.via")} ${t.answered_via}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {answers.length > 0 && (
            <>
              <h2>{c("inbox.from_suppliers")}</h2>
              <ul className="list">
                {answers.map((a) => <li key={a.request_id}>{vendorName(a.vendor_id)} · {a.request_id}: <strong>{a.answer || a.status}</strong></li>)}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}

/** Why it is here, in words, and where else the question went (Slack, email). */
function Why({ item }: { item: Item }) {
  const deliveries = item.task?.deliveries ?? [];
  const channels = [...new Set(deliveries.map((d) => d.channel).filter((ch) => ch !== "inapp"))].map((ch) => c(`inbox.channel_${ch}`));
  const reason = item.queue?.reason ?? item.task?.question ?? "";
  // a reason that lists machine codes is shown as those codes in words, one per line
  const codes = [...new Set(reason.match(/\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b/g) ?? [])];
  return (
    <div className="why">
      <strong>{c("inbox.why")}</strong>
      {codes.length ? (
        <ul className="flag-list">{codes.map((code) => <li key={code} title={code}>{explain(code)}</li>)}</ul>
      ) : (
        <p>{reason}</p>
      )}
      {channels.length > 0 && item.task && (
        <p className="muted small">{c("inbox.also_asked", { role: item.task.role.replace(/_/g, " "), channels: channels.join(", ") })}</p>
      )}
    </div>
  );
}

function SupplierChecks({ vendor }: { vendor?: RetailerVendor }) {
  if (!vendor) return null;
  return (
    <div className="stack-tight">
      <h3>{c("inbox.checks")}</h3>
      <ul className="checks">
        {(vendor.checks ?? []).map((ch, i) => (
          <li key={i} className={ch.status}><Icon name={ch.status === "pass" ? "check" : "x"} size={15} /> {ch.doc_type}: {explain(ch.code)} {ch.detail}</li>
        ))}
      </ul>
    </div>
  );
}

const kindLabel = (item: Item) =>
  item.kind === "question" && item.task?.resume.startsWith("job:") ? c("inbox.kind_run_approval") : c(`inbox.kind_${item.kind}`);

/** What the asking agent knew (supplier, store, value against the limit), for questions that are
 *  not backed by a saved order or listing, e.g. an order a replenishment run is holding. */
function TaskFacts({ task }: { task?: HumanTask }) {
  const params = Object.entries(task?.request.params ?? {});
  if (!task || !params.length) return null;
  return (
    <div className="stack-tight">
      <dl className="facts-head">
        {params.map(([k, v]) => {
          const label = c(`inbox.param_${k}`);
          return <div key={k}><dt>{label.startsWith("⟨") ? k.replace(/_/g, " ") : label}</dt><dd>{String(v)}</dd></div>;
        })}
      </dl>
      {task.resume.startsWith("job:") && <p className="muted small">{c("inbox.run_note")} <Link to="/retailer/orders">{c("nav.orders")} →</Link></p>}
    </div>
  );
}
