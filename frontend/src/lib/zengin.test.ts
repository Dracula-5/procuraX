import { describe, expect, it } from "vitest";
import shared from "./zengin.cases.json";
import { checkDigits, checkKanaField, toZenginKana } from "./zengin";

// The same cases are asserted against backend/app/domain/zengin.py in tests/unit/test_zengin_fields.py.
describe("toZenginKana mirrors the backend", () => {
  it.each(shared.cases)("$input", (c) => {
    const result = toZenginKana(c.input);
    if (c.value === null) {
      expect(result.invalid).toEqual(c.invalid);
    } else {
      expect(result).toEqual({ value: c.value, invalid: [] });
    }
  });
});

describe("field checks", () => {
  it("reports length after conversion, counting dakuten as characters", () => {
    // ガ becomes ｶﾞ (two bank characters), so 8 × ガ is 16 characters.
    expect(checkKanaField("ガ".repeat(8), 15).issue).toEqual({ kind: "too_long", max: 15, length: 16 });
    expect(checkKanaField("ガ".repeat(7), 15).issue).toBeNull();
  });

  it("names the characters a bank cannot accept", () => {
    expect(checkKanaField("ミナト商事", 30)).toEqual({ preview: "ﾐﾅﾄ", issue: { kind: "invalid_chars", chars: "商事" } });
  });

  it("requires a value", () => {
    expect(checkKanaField("  ", 30).issue).toEqual({ kind: "required" });
  });

  it("checks exact and maximum digit counts", () => {
    expect(checkDigits("0999", 4, 4)).toBeNull();
    expect(checkDigits("999", 4, 4)).toEqual({ kind: "digits", length: 4 });
    expect(checkDigits("12a", 3, 3)).toEqual({ kind: "digits", length: 3 });
    expect(checkDigits("1234567", null, 7)).toBeNull();
    expect(checkDigits("12345678", null, 7)).toEqual({ kind: "digits", length: 7 });
    expect(checkDigits("", null, 7)).toEqual({ kind: "required" });
  });
});
