import { useEffect, useRef, useState } from "react";
import type { ChatReply, Listing, ListingDraftView, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { downscale } from "../lib/image";
import { browserSpeechAvailable, speak, startListening } from "../lib/speech";
import { LiveVoice, type LiveMessage } from "../lib/voice";
import { Icon } from "../lib/icons";
import { ListingResult, photoSrc, type Photo } from "./ListingCard";

type Text = { kind: "text"; role: "user" | "model" | "system"; text: string; image?: string; working?: boolean };
type Card = {
  kind: "listing"; listing: Listing | null; readback: string; autoApproved: boolean; reasons: string[]; photo: Photo | null;
};
type Line = Text | Card;
type Mode = "unknown" | "live" | "browser";
/** Adding a product, one question at a time: photo → what it is (price, how many) → label text. */
type Flow = { step: "describe" | "label"; photo: Photo | null; text: string } | null;

/** The vendor's home: she talks (or types) to her assistant, and adds products by sending a photo
 *  and saying what it is. The listing comes back into the chat as a card she answers right there. */
export function Talk({ vendor, t, onChange, startListing }: {
  vendor: Vendor; t: T; onChange: () => void; startListing: number;
}) {
  const [lines, setLines] = useState<Line[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [flow, setFlowState] = useState<Flow>(null);
  const flowRef = useRef<Flow>(null);
  const mode = useRef<Mode>("unknown");
  const live = useRef<LiveVoice | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const packaged = vendor.business_profile === "packaged_food";

  const setFlow = (f: Flow) => {
    flowRef.current = f;
    setFlowState(f);
  };

  useEffect(() => {
    // scrollIntoView returns a Promise in current Chrome; an effect must not return it
    void bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [lines]);
  useEffect(() => {
    setLines([{ kind: "text", role: "model", text: t("chat.greeting", { name: vendor.display_name }) }]);
    setFlow(null);
    return () => {
      live.current?.close();
      live.current = null;
      mode.current = "unknown";
    };
  }, [vendor.id, vendor.display_name, t]);
  // "Add a product" elsewhere in the app starts the same flow here
  useEffect(() => {
    if (startListing > 0) beginListing();
  }, [startListing]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = (line: Line) => setLines((l) => [...l, line]);
  const say = (key: string) => add({ kind: "text", role: "model", text: t(key) });

  function beginListing() {
    setFlow({ step: "describe", photo: null, text: "" });
    say("ui.flow.start");
  }

  function cancelListing() {
    setFlow(null);
    say("ui.flow.cancelled");
  }

  /** Photo + her words → the retailer's agents check it and write the listing → a card in the chat. */
  async function draftListing(photo: Photo | null, words: string, label: string | null, spoken: boolean) {
    setFlow(null);
    const working: Text = { kind: "text", role: "system", text: t("ui.products.working"), working: true };
    add(working);
    setBusy(true);
    try {
      const view = await api<ListingDraftView>(VENDOR_URL, `/vendors/${vendor.id}/listings/draft`, {
        method: "POST",
        json: { vendor_id: vendor.id, language: vendor.language, transcript: words, label_text: label,
                photo_b64: photo?.data_b64 ?? null, photo_mime: photo?.content_type ?? "image/jpeg" },
      });
      const card: Card = {
        kind: "listing", listing: view.result.listing ?? null, readback: view.readback,
        autoApproved: Boolean(view.approval?.auto_approve), reasons: view.approval?.reasons ?? [], photo,
      };
      setLines((l) => l.map((x) => (x === working ? card : x)));
      if (spoken && view.readback) speak(view.readback, vendor.language);
      onChange();
    } catch (e) {
      setLines((l) => l.map((x) => (x === working ? { kind: "text", role: "system", text: String(e) } : x)));
    } finally {
      setBusy(false);
    }
  }

  async function send(message: string, spoken: boolean) {
    if (!message.trim()) return;
    add({ kind: "text", role: "user", text: message });
    const f = flowRef.current;
    if (f?.step === "describe") {
      if (packaged) {
        setFlow({ ...f, step: "label", text: message });
        say("ui.flow.label");
      } else {
        void draftListing(f.photo, message, null, spoken);
      }
      return;
    }
    if (f?.step === "label") {
      void draftListing(f.photo, f.text, message, spoken);
      return;
    }
    setBusy(true);
    try {
      const reply = await api<ChatReply>(VENDOR_URL, `/vendors/${vendor.id}/chat`, {
        method: "POST",
        json: { text: message, language: vendor.language },
      });
      add({ kind: "text", role: "model", text: reply.reply });
      if (spoken) speak(reply.reply, reply.language);
      await showListings(reply.events?.filter((e) => e.type === "listing").map((e) => String(e.data?.listing_id ?? "")) ?? []);
      if (reply.events?.length) onChange();
    } catch (e) {
      add({ kind: "text", role: "system", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  /** The assistant made a listing by itself (a model was talking with her): show it as a card too. */
  async function showListings(ids: string[]) {
    const wanted = ids.filter(Boolean);
    if (!wanted.length) return;
    const all = await api<Listing[]>(VENDOR_URL, `/vendors/${vendor.id}/listings`).catch(() => [] as Listing[]);
    for (const l of all.filter((x) => wanted.includes(x.id))) {
      add({ kind: "listing", listing: l, readback: "", autoApproved: l.status === "published", reasons: [], photo: null });
    }
  }

  function onLive(m: LiveMessage) {
    if (m.type === "error") {
      // No Live model on the server: use the phone's own speech recognition instead.
      mode.current = "browser";
      live.current?.close();
      live.current = null;
      add({ kind: "text", role: "system", text: t("ui.voice_off") });
    } else if (m.type === "transcript") {
      add({ kind: "text", role: m.role, text: m.text });
    } else {
      onChange();
    }
  }

  async function press() {
    setListening(true);
    // while adding a product, her words fill the listing here rather than going to the live model
    if (!flowRef.current) {
      if (mode.current === "unknown") {
        const url = VENDOR_URL.replace(/^http/, "ws") + `/voice/${vendor.id}`;
        live.current = new LiveVoice(url, vendor.language, onLive);
        mode.current = "live";
      }
      if (mode.current === "live" && live.current) {
        try {
          await live.current.press();
          return;
        } catch {
          mode.current = "browser";
        }
      }
    }
    stop.current = startListening(vendor.language, (heard) => void send(heard, true));
  }

  function release() {
    setListening(false);
    live.current?.release();
    stop.current?.();
    stop.current = null;
  }

  async function photo(file: File | undefined) {
    if (!file) return;
    const img = await downscale(file);
    add({ kind: "text", role: "user", text: "", image: photoSrc(img) });
    // the assistant has it too, for anything she then asks it to do with the photo
    void api(VENDOR_URL, `/vendors/${vendor.id}/chat/photo`, {
      method: "POST",
      json: { doc_type: "photo", ...img, filename: file.name },
    }).catch(() => undefined);
    const f = flowRef.current;
    setFlow({ step: f?.step ?? "describe", photo: img, text: f?.text ?? "" });
    if (!f || f.step === "describe") say("ui.flow.got_photo");
  }

  return (
    <section className="talk">
      <div className="captions" aria-live="polite">
        {lines.map((l, i) =>
          l.kind === "listing" ? (
            <div key={i} className="bubble model card">
              <ListingResult vendor={vendor} t={t} listing={l.listing} readback={l.readback}
                autoApproved={l.autoApproved} reasons={l.reasons} ownPhoto={l.photo} onDone={onChange} />
            </div>
          ) : l.image ? (
            <div key={i} className="bubble user photo"><img src={l.image} alt={t("ui.photo")} /></div>
          ) : (
            <p key={i} className={`bubble ${l.role}${l.working ? " working" : ""}`}>{l.text}</p>
          ),
        )}
        <div ref={bottom} />
      </div>
      <div className="talk-input">
      {listening && <p className="listening" aria-live="polite">● {t("ui.listening")}</p>}
      <div className="quick">
        {flow ? (
          <>
            {flow.step === "label" && (
              <button type="button" className="chip-btn" onClick={() => void draftListing(flow.photo, flow.text, null, false)}>
                {t("ui.flow.skip")}
              </button>
            )}
            <button type="button" className="chip-btn" onClick={cancelListing}><Icon name="x" size={15} /> {t("ui.flow.cancel")}</button>
          </>
        ) : (
          <>
            <button type="button" className="chip-btn primary" disabled={busy} onClick={beginListing}>
              <Icon name="camera" size={15} /> {t("ui.chips.add")}
            </button>
            {(["deals", "rules", "today"] as const).map((k) => (
              <button key={k} type="button" className="chip-btn" disabled={busy} onClick={() => void send(t(`ui.chips.${k}`), false)}>
                {t(`ui.chips.${k}`)}
              </button>
            ))}
          </>
        )}
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send(text, false);
          setText("");
        }}
      >
        <label className={`camera ${flow && !flow.photo ? "nudge" : ""}`}>
          <Icon name="camera" size={20} />
          <span className="sr-only">{t("ui.photo")}</span>
          <input type="file" accept="image/*" capture="environment" onChange={(e) => {
            void photo(e.target.files?.[0]);
            e.target.value = ""; // the same photo can be sent again
          }} />
        </label>
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={flow?.step === "describe" ? t("ui.products.describe") : flow?.step === "label" ? t("ui.products.label_text") : t("ui.type_here")}
          lang={vendor.language}
        />
        {/* Like any chat app: an empty box offers the mic (hold to speak); typing turns it into send. */}
        {text.trim() ? (
          <button type="submit" className="round-action" disabled={busy} aria-label={t("ui.send")} title={t("ui.send")}>
            <SendIcon />
          </button>
        ) : (
          <button
            type="button"
            className={`round-action ${listening ? "on" : ""}`}
            disabled={busy || (!browserSpeechAvailable && mode.current === "browser")}
            onPointerDown={() => void press()}
            onPointerUp={release}
            onPointerLeave={() => listening && release()}
            aria-label={t("ui.hold_to_talk")}
            title={t("ui.hold_to_talk")}
          >
            <MicIcon />
          </button>
        )}
      </form>
      </div>
    </section>
  );
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden fill="currentColor">
      <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden fill="currentColor">
      <path d="M3.4 20.4 21 12 3.4 3.6 3.39 10.2 15 12 3.39 13.8Z" />
    </svg>
  );
}
