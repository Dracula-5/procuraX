import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/useAuth";
import { applyLanguage, isLanguage, LANGUAGES, type Language } from "../i18n";
import { api } from "../lib/api";
import type { Me } from "../lib/types";

const NAMES: Record<Language, string> = { en: "English", ja: "日本語" };

/** Switches the UI language. Signed in, the choice is saved to the user's profile; otherwise to this device. */
export function LanguageSwitcher({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  const { me, updateMe } = useAuth();
  const current: Language = isLanguage(i18n.language) ? i18n.language : "en";

  const change = (value: string) => {
    if (!isLanguage(value)) return;
    void applyLanguage(value);
    if (me) {
      // Best effort: the language has already changed locally; a failed save only affects other devices.
      void api
        .patch<Me>("/auth/me/preferences", { locale: value })
        .then(updateMe)
        .catch(() => undefined);
    }
  };

  return (
    <label className={className ?? "inline-flex items-center gap-1.5 text-xs text-muted"}>
      <Languages className="size-4" aria-hidden />
      <span className="sr-only">{t("layout.language")}</span>
      <select
        value={current}
        onChange={(e) => change(e.target.value)}
        aria-label={t("layout.language")}
        className="rounded-md border border-border bg-surface px-1.5 py-1 text-xs text-fg"
      >
        {LANGUAGES.map((lang) => (
          <option key={lang} value={lang} lang={lang}>
            {NAMES[lang]}
          </option>
        ))}
      </select>
    </label>
  );
}
