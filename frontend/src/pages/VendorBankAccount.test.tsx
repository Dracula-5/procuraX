import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { applyLanguage } from "../i18n";
import { apiError, makeMe, mockApi, renderWithProviders } from "../test/utils";
import { VendorBankAccount } from "./VendorBankAccount";

const procurement = makeMe({ roles: ["procurement_officer"], permissions: ["vendor:read", "vendor:manage"] });
const vendor = { id: "v-1", name: "Minato Supplies", status: "approved" };

function setup(handlers = {}) {
  const api = mockApi({
    "GET /vendors/v-1": () => vendor,
    "GET /vendors/v-1/bank-account": () => ({ vendor_id: "v-1", bank_account: null }),
    ...handlers,
  });
  renderWithProviders(<VendorBankAccount />, { me: procurement, path: "/app/vendors/:id/bank-account", route: "/app/vendors/v-1/bank-account" });
  return api;
}

async function fill(fields: Record<string, string>) {
  for (const [label, value] of Object.entries(fields)) {
    // Field labels also contain their hint text, so match the label by substring.
    const input = screen.getByLabelText(label, { exact: false });
    await userEvent.clear(input);
    await userEvent.type(input, value);
  }
}

describe("Vendor bank account screen", () => {
  it("shows Zengin problems inline while typing and blocks saving", async () => {
    setup();
    await screen.findByText("Bank account · Minato Supplies");
    await fill({ "Account holder (kana)": "ミナト商事" });
    expect(await screen.findByText("Cannot be sent to the bank: 商事")).toBeInTheDocument();
    await fill({ "Bank name (kana)": "アイウエオカキクケコサシスセソタ" });
    expect(screen.getByText("Too long for the bank file: 16 of 15 characters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("previews the bank form and saves a valid account", async () => {
    const { calls } = setup({
      "PUT /vendors/v-1/bank-account": () => ({ vendor_id: "v-1", bank_account: { bank_code: "0999", bank_name_kana: "ﾃｽﾄｷﾞﾝｺｳ", branch_code: "001", branch_name_kana: "ﾎﾝﾃﾝ", account_type: "1", account_number: "****567", holder_name_kana: "ｶ)ﾐﾅﾄ" } }),
    });
    await screen.findByText("Bank account · Minato Supplies");
    await fill({
      "Bank code": "0999",
      "Bank name (kana)": "テストギンコウ",
      "Branch code": "001",
      "Branch name (kana)": "ほんてん",
      "Account number": "1234567",
      "Account holder (kana)": "カ）ミナト",
    });
    expect(screen.getByText("Sent to the bank as ﾎﾝﾃﾝ (4/15)")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await screen.findByText("Saved.");
    expect(calls.find((c) => c.key === "PUT /vendors/v-1/bank-account")?.body).toMatchObject({ bank_code: "0999", holder_name_kana: "カ）ミナト" });
    expect(screen.getByText("****567")).toBeInTheDocument();
  });

  it("puts a server refusal next to the field, translated", async () => {
    await applyLanguage("ja");
    setup({
      "PUT /vendors/v-1/bank-account": () =>
        apiError(422, "business_rule_violation", "Not allowed", { reason: "zengin_too_long", field: "holder_name_kana" }),
    });
    await screen.findByText("振込口座・Minato Supplies");
    await fill({ 金融機関コード: "0999", "金融機関名（カナ）": "テスト", 支店コード: "001", "支店名（カナ）": "ホンテン", 口座番号: "1", "口座名義（カナ）": "ミナト" });
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByText("口座名義が振込データには長すぎます。")).toBeInTheDocument());
  });
});
