// Validation messages for 422 responses. Kept separate from errors.ts so api.ts can use it
// without an import cycle.
import i18n from "../i18n";

/** One validation message, translated by Pydantic error type outside English. */
export function validationMessage(type: string | undefined, englishMessage: string): string {
  if (!i18n.language?.startsWith("ja")) return englishMessage.replace(/^Value error, /, "");
  const key = type && i18n.exists(`errors.validation.${type}`) ? `errors.validation.${type}` : "errors.validation.invalid";
  return String(i18n.t(key as never));
}
