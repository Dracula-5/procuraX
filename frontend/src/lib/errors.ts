// Turns API errors into text for the active language. The API returns stable codes
// (`error.code`, and for business-rule refusals `error.details.reason`); its English `message` is
// shown as-is in English and as secondary detail elsewhere.
import i18n from "../i18n";
import { ApiError } from "./api";

export interface ErrorText {
  message: string;
  /** The server's own (English) sentence, shown smaller when the main message is a translation. */
  detail: string | null;
  requestId: string | null;
}

const english = () => !i18n.language?.startsWith("ja");
const has = (key: string) => i18n.exists(key);
const tr = (key: string, params?: Record<string, unknown>) => String(i18n.t(key as never, params as never));

/** Label for a request field named in `details.field`, e.g. "holder_name_kana". */
export function fieldLabel(field: string): string {
  return has(`errors.fields.${field}`) ? tr(`errors.fields.${field}`) : field.replace(/_/g, " ");
}

export function describeError(error: unknown): ErrorText {
  if (error instanceof ApiError) {
    const details = error.details && typeof error.details === "object" && !Array.isArray(error.details) ? (error.details as Record<string, unknown>) : {};
    const reason = typeof details.reason === "string" ? details.reason : null;
    if (english()) return { message: error.message, detail: null, requestId: error.requestId };
    if (reason && has(`errors.reasons.${reason}`)) {
      const field = typeof details.field === "string" ? fieldLabel(details.field) : "";
      return { message: tr(`errors.reasons.${reason}`, { field }), detail: error.message, requestId: error.requestId };
    }
    const key = has(`errors.codes.${error.code}`) ? `errors.codes.${error.code}` : "errors.generic";
    return { message: tr(key), detail: error.message, requestId: error.requestId };
  }
  // fetch() rejects with a TypeError when the network or API is unreachable.
  if (error instanceof TypeError) return { message: tr("errors.network"), detail: null, requestId: null };
  if (error instanceof Error) return { message: error.message, detail: null, requestId: null };
  return { message: tr("errors.generic"), detail: null, requestId: null };
}
