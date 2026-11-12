import { describe, expect, it } from "vitest";
import { applyLanguage } from "../i18n";
import { currentFiscalYear, date, money, monthNames, percent } from "./format";

describe("formatting follows the UI language", () => {
  it("formats yen without decimals in both languages", async () => {
    expect(money("1234567", "JPY")).toBe("¥1,234,567");
    await applyLanguage("ja");
    // ja-JP uses the full-width yen sign.
    expect(money("1234567", "JPY")).toBe("￥1,234,567");
    expect(money(null)).toBe("—");
  });

  it("keeps two decimals for other currencies", async () => {
    expect(money("1234.5", "USD")).toBe("$1,234.50");
    await applyLanguage("ja");
    expect(money("1234.5", "USD")).toBe("$1,234.50");
  });

  it("writes dates in each language's convention", async () => {
    expect(date("2026-04-01T00:00:00Z")).toMatch(/1 Apr 2026/);
    await applyLanguage("ja");
    expect(date("2026-04-01T00:00:00Z")).toMatch(/2026\/04\/01|2026年4月1日/);
  });

  it("formats percentages and month names", async () => {
    expect(percent("0.925")).toBe("92.5%");
    expect(monthNames()[3]).toBe("April");
    await applyLanguage("ja");
    expect(monthNames()[3]).toBe("4月");
  });

  it("derives the fiscal year from its start month", () => {
    expect(currentFiscalYear(4, new Date(2026, 2, 31))).toBe(2025);
    expect(currentFiscalYear(4, new Date(2026, 3, 1))).toBe(2026);
  });
});
