// Client-side mirror of backend/app/domain/zengin.py `to_zengin_kana` and the account field rules,
// so the bank-account forms can show invalid characters while the user types. The API validates
// again on save. Both implementations are tested against the same cases in zengin.cases.json.

const FULL = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンヰヱ";
const HALF = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜｦﾝｲｴ";
const SMALL_FROM = "ァィゥェォッャュョヮ";
const SMALL_TO = "ｱｲｳｴｵﾂﾔﾕﾖﾜ";

const TO_HALF = new Map<string, string>();
[...FULL].forEach((char, i) => TO_HALF.set(char, HALF[i]));
[...SMALL_FROM].forEach((char, i) => TO_HALF.set(char, SMALL_TO[i]));
TO_HALF.set("゙", "ﾞ"); // combining dakuten → half-width ﾞ
TO_HALF.set("゚", "ﾟ"); // combining handakuten → half-width ﾟ
TO_HALF.set("ー", "-"); // long vowel mark

const ALLOWED = new Set([...HALF, "ﾞ", "ﾟ", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ().-/,\\｢｣"]);

export interface KanaResult {
  /** The name as it will be stored and written to the transfer file. */
  value: string;
  /** Characters that cannot appear in a Zengin record (deduplicated, in input order). */
  invalid: string[];
}

export function toZenginKana(text: string): KanaResult {
  // NFKC folds half-width kana and full-width ASCII; NFD then splits ガ into カ + combining ゛.
  const normalized = text.normalize("NFKC").normalize("NFD").toUpperCase();
  const out: string[] = [];
  const invalid: string[] = [];
  for (let char of normalized) {
    const code = char.codePointAt(0)!;
    if (code >= 0x3041 && code <= 0x3096) char = String.fromCodePoint(code + 0x60); // hiragana → katakana
    const mapped = TO_HALF.get(char) ?? char;
    if ([...mapped].every((piece) => ALLOWED.has(piece))) out.push(mapped);
    else if (!invalid.includes(char)) invalid.push(char);
  }
  return { value: out.join(""), invalid };
}

export type ZenginIssue =
  | { kind: "invalid_chars"; chars: string }
  | { kind: "too_long"; max: number; length: number }
  | { kind: "digits"; length: number }
  | { kind: "required" };

/** Field rules from validate_account(): digits for codes, a converted length limit for names. */
export function checkKanaField(text: string, max: number): { preview: string; issue: ZenginIssue | null } {
  if (!text.trim()) return { preview: "", issue: { kind: "required" } };
  const { value, invalid } = toZenginKana(text);
  if (invalid.length) return { preview: value, issue: { kind: "invalid_chars", chars: invalid.join("") } };
  if ([...value].length > max) return { preview: value, issue: { kind: "too_long", max, length: [...value].length } };
  return { preview: value, issue: null };
}

export function checkDigits(text: string, exact: number | null, max: number): ZenginIssue | null {
  if (!text) return { kind: "required" };
  if (!/^\d+$/.test(text)) return { kind: "digits", length: exact ?? max };
  if (exact !== null ? text.length !== exact : text.length > max) return { kind: "digits", length: exact ?? max };
  return null;
}

export const NAME_LIMITS = { bank_name_kana: 15, branch_name_kana: 15, holder_name_kana: 30, consignor_name_kana: 40 } as const;
