import en from "../i18n/en.json";
import te from "../i18n/te.json";
import id from "../i18n/id.json";
import consoleEn from "../i18n/console.en.json";

type Tree = { [key: string]: string | string[] | Tree };
const packs: Record<string, Tree> = { en, te, id } as Record<string, Tree>;

function lookup(tree: Tree, key: string): unknown {
  return key.split(".").reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Tree)[part] : undefined), tree);
}

function render(template: string, params: Record<string, string | number> = {}): string {
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? String(params[k]) : m));
}

export type T = ((key: string, params?: Record<string, string | number>) => string) & {
  words: (key: string) => string[];
  lang: string;
};

/** A language's name in itself ("English", "తెలుగు", "Bahasa Indonesia"), for pickers. */
export function languageName(lang: string, short = false): string {
  const name = packs[lang] ? lookup(packs[lang], short ? "language_short" : "language_name") : undefined;
  return typeof name === "string" ? name : lang;
}

/** Strings for one vendor language (falls back to English for a missing key, visibly). */
export function strings(lang: string): T {
  const pack = packs[lang] ?? packs.en;
  const t = ((key: string, params?: Record<string, string | number>) => {
    const value = lookup(pack, key) ?? lookup(packs.en, key);
    return typeof value === "string" ? render(value, params) : `⟨${key}⟩`;
  }) as T;
  t.words = (key) => {
    const value = lookup(pack, key);
    return Array.isArray(value) ? value : [];
  };
  t.lang = lang;
  return t;
}

/** Retailer console strings. */
export function c(key: string, params?: Record<string, string | number>): string {
  const value = lookup(consoleEn as Tree, key);
  return typeof value === "string" ? render(value, params) : `⟨${key}⟩`;
}

export const SPEECH_LOCALE: Record<string, string> = { te: "te-IN", id: "id-ID", en: "en-IN" };
