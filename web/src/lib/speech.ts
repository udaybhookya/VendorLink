import { SPEECH_LOCALE } from "./i18n";

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: unknown) => void) | null;
  start(): void;
  stop(): void;
};

const Ctor = (window as unknown as Record<string, unknown>).SpeechRecognition ??
  (window as unknown as Record<string, unknown>).webkitSpeechRecognition;

export const browserSpeechAvailable = typeof Ctor === "function";

/** Push-to-talk with the browser's own speech recognition (fallback when no Live model). */
export function startListening(lang: string, onText: (text: string) => void): () => void {
  if (!browserSpeechAvailable) return () => undefined;
  const rec = new (Ctor as new () => Recognition)();
  rec.lang = SPEECH_LOCALE[lang] ?? lang;
  rec.interimResults = false;
  rec.continuous = true;
  let text = "";
  rec.onresult = (e) => {
    text = Array.from(e.results).map((r) => r[0].transcript).join(" ");
  };
  rec.onend = () => text.trim() && onText(text.trim());
  rec.onerror = () => undefined;
  rec.start();
  return () => rec.stop();
}

export function speak(text: string, lang: string): void {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = SPEECH_LOCALE[lang] ?? lang;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}
