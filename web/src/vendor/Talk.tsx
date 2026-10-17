import { useEffect, useRef, useState } from "react";
import type { ChatReply, Vendor } from "../types/generated";
import { api, VENDOR_URL } from "../lib/api";
import type { T } from "../lib/i18n";
import { downscale } from "../lib/image";
import { browserSpeechAvailable, speak, startListening } from "../lib/speech";
import { LiveVoice, type LiveMessage } from "../lib/voice";
import { Icon } from "../lib/icons";

type Line = { role: "user" | "model" | "system"; text: string };
type Mode = "unknown" | "live" | "browser";

export function Talk({ vendor, t, onChange }: { vendor: Vendor; t: T; onChange: () => void }) {
  const [lines, setLines] = useState<Line[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const mode = useRef<Mode>("unknown");
  const live = useRef<LiveVoice | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // scrollIntoView returns a Promise in current Chrome; an effect must not return it
    void bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);
  useEffect(() => {
    setLines([{ role: "model", text: t("chat.greeting", { name: vendor.display_name }) }]);
    return () => {
      live.current?.close();
      live.current = null;
      mode.current = "unknown";
    };
  }, [vendor.id, vendor.display_name, t]);

  const add = (line: Line) => setLines((l) => [...l, line]);

  async function send(message: string, spoken: boolean) {
    if (!message.trim()) return;
    add({ role: "user", text: message });
    setBusy(true);
    try {
      const reply = await api<ChatReply>(VENDOR_URL, `/vendors/${vendor.id}/chat`, {
        method: "POST",
        json: { text: message, language: vendor.language },
      });
      add({ role: "model", text: reply.reply });
      if (spoken) speak(reply.reply, reply.language);
      if (reply.events?.length) onChange();
    } catch (e) {
      add({ role: "system", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  function onLive(m: LiveMessage) {
    if (m.type === "error") {
      // No Live model on the server: use the phone's own speech recognition instead.
      mode.current = "browser";
      live.current?.close();
      live.current = null;
      add({ role: "system", text: t("ui.voice_off") });
    } else if (m.type === "transcript") {
      add({ role: m.role, text: m.text });
    } else {
      onChange();
    }
  }

  async function press() {
    setListening(true);
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
    await api(VENDOR_URL, `/vendors/${vendor.id}/chat/photo`, {
      method: "POST",
      json: { doc_type: "photo", ...img, filename: file.name },
    });
    add({ role: "system", text: t("ui.photo_added") });
  }

  return (
    <section className="talk">
      <div className="captions" aria-live="polite">
        {lines.map((l, i) => (
          <p key={i} className={`bubble ${l.role}`}>
            {l.text}
          </p>
        ))}
        <div ref={bottom} />
      </div>
      {listening && <p className="listening" aria-live="polite">● {t("ui.listening")}</p>}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send(text, false);
          setText("");
        }}
      >
        <label className="camera">
          <Icon name="camera" size={20} />
          <span className="sr-only">{t("ui.photo")}</span>
          <input type="file" accept="image/*" capture="environment" onChange={(e) => void photo(e.target.files?.[0])} />
        </label>
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t("ui.type_here")}
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
