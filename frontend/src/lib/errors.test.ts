import { describe, expect, it } from "vitest";
import { applyLanguage } from "../i18n";
import { ApiError } from "./api";
import { describeError } from "./errors";
import { makeLabels } from "./labels";
import i18n from "../i18n";

const refusal = new ApiError(422, "business_rule_violation", "Choose another user to delegate this approval", { reason: "delegation_self" }, "req-1");

describe("API errors are shown in the UI language", () => {
  it("keeps the server's sentence in English", () => {
    expect(describeError(refusal)).toEqual({ message: "Choose another user to delegate this approval", detail: null, requestId: "req-1" });
  });

  it("translates a refusal by its reason code, keeping the server text as detail", async () => {
    await applyLanguage("ja");
    const text = describeError(refusal);
    expect(text.message).toBe("自分以外のユーザーを選んでください。");
    expect(text.detail).toBe("Choose another user to delegate this approval");
  });

  it("names the field in a Zengin refusal", async () => {
    await applyLanguage("ja");
    const error = new ApiError(422, "business_rule_violation", "Not allowed: 株", { reason: "zengin_invalid_characters", field: "holder_name_kana" }, null);
    expect(describeError(error).message).toBe("口座名義に振込データで使えない文字が含まれています。");
  });

  it("falls back to the error code, then to a generic message", async () => {
    await applyLanguage("ja");
    expect(describeError(new ApiError(409, "conflict", "Already exists", null, null)).message).toBe("現在の状態と矛盾しています。再読み込みしてからお試しください。");
    expect(describeError(new ApiError(418, "teapot", "No", null, null)).message).toBe("問題が発生しました。");
  });

  it("explains network failures", async () => {
    await applyLanguage("ja");
    expect(describeError(new TypeError("Failed to fetch")).message).toContain("API に接続できませんでした");
  });

  it("translates validation messages by type", async () => {
    const error = new ApiError(422, "validation_error", "Request validation failed", [{ loc: ["body", "title"], msg: "String should have at least 3 characters", type: "string_too_short" }], null);
    expect(error.fieldErrors()).toEqual({ title: "String should have at least 3 characters" });
    await applyLanguage("ja");
    expect(error.fieldErrors()).toEqual({ title: "短すぎます" });
  });
});

describe("rule and audit wording", () => {
  const hit = {
    rule_id: "APR-001",
    message: "Amount ¥600,000 exceeds the auto-approval limit ¥30,000.",
    evidence: { amount: "600000", threshold: "30000", approver_role: "manager" },
  };

  it("shows the engine's own sentence in English", () => {
    expect(makeLabels(i18n.t, i18n).ruleMessage(hit, "JPY")).toBe(hit.message);
  });

  it("rebuilds the sentence from evidence in Japanese", async () => {
    await applyLanguage("ja");
    const labels = makeLabels(i18n.t, i18n);
    expect(labels.ruleMessage(hit, "JPY")).toBe("金額 ￥600,000 が自動承認上限 ￥30,000 を超えています。");
    expect(labels.ruleTitle("APR-001", "Manager approval")).toBe("上長承認");
    expect(labels.auditSummary("approval.delegated", "Approval delegated to Kim")).toBe("承認を代理者に委任");
    // Unknown codes fall back to the server text rather than showing a key.
    expect(labels.auditSummary("something.new", "Something new happened")).toBe("Something new happened");
    expect(labels.prStatus("brand_new_status")).toBe("brand new status");
  });

  it("computes the budget overrun for BUD-002", async () => {
    await applyLanguage("ja");
    const message = makeLabels(i18n.t, i18n).ruleMessage(
      { rule_id: "BUD-002", message: "x", evidence: { budget: "1000000", committed: "900000", requested: "250000" } },
      "JPY",
    );
    expect(message).toBe("この申請により年度予算を ￥150,000 超過します。");
  });
});
