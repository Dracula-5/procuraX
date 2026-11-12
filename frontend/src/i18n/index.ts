import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { en } from "./locales/en";

export const LANGUAGES = ["en", "ja"] as const;
export type Language = (typeof LANGUAGES)[number];
const STORAGE_KEY = "procurax.lang";

export const isLanguage = (value: unknown): value is Language => value === "en" || value === "ja";

// English ships in the main bundle (it is also the fallback); other languages load on demand.
const loaders: Record<Exclude<Language, "en">, () => Promise<typeof en>> = {
  ja: () => import("./locales/ja").then((m) => m.ja),
};

/** Before sign-in: the last choice on this device, else the browser language. */
export function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLanguage(stored)) return stored;
  } catch {
    /* storage unavailable */
  }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ja") ? "ja" : "en";
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  partialBundledLanguages: true,
  interpolation: { escapeValue: false }, // React escapes
  returnNull: false,
});

export async function loadLanguage(lang: Language): Promise<void> {
  if (lang === "en" || i18n.hasResourceBundle(lang, "translation")) return;
  i18n.addResourceBundle(lang, "translation", await loaders[lang]());
}

/** Switch the UI language (loading it first if needed) and remember it on this device. */
export async function applyLanguage(lang: Language): Promise<void> {
  await loadLanguage(lang);
  if (i18n.language !== lang) await i18n.changeLanguage(lang);
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* storage unavailable */
  }
  if (typeof document !== "undefined") document.documentElement.lang = lang;
}

/** Called once before the first render so a Japanese user never sees an English flash. */
export async function initLanguage(): Promise<void> {
  try {
    await applyLanguage(initialLanguage());
  } catch {
    /* the chunk failed to load: stay in English */
  }
}

export default i18n;
