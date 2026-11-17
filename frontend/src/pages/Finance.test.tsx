import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Contract, RatesResponse } from "../lib/types";
import { apiError, makeMe, mockApi, renderWithProviders } from "../test/utils";
import { Contracts } from "./Contracts";
import { ExchangeRates } from "./ExchangeRates";
import { Ledger } from "./Ledger";

const rates: RatesResponse = {
  base_currency: "JPY",
  supported_currencies: ["USD", "EUR"],
  ecb_enabled: false,
  latest: [{ id: "r-1", currency: "USD", rate: "150.25000000", valid_on: "2026-11-12", source: "manual", created_at: "2026-11-12T01:00:00Z" }],
  history: [],
};

const draft: Contract = {
  id: "c-1",
  number: "CT-2026-000001",
  vendor_id: "v-1",
  vendor_name: "Minato Software K.K.",
  title: "Licences framework",
  categories: ["software"],
  starts_on: "2026-04-01",
  ends_on: "2027-03-31",
  value_cap: "5000000",
  currency: "JPY",
  status: "draft",
  terminated_reason: null,
  committed: "1200000",
  base_currency: "JPY",
  document: null,
  expiry_reminded_at: null,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
  allowed_actions: ["activate", "edit", "terminate"],
};

const procurement = makeMe({ roles: ["procurement_officer"], permissions: ["vendor:read", "vendor:manage"] });

describe("Contracts screen", () => {
  it("shows cap and committed spend and activates a draft", async () => {
    const { calls } = mockApi({
      "GET /contracts": () => [draft],
      "GET /vendors": () => ({ items: [], total: 0 }),
      "GET /fx/rates": () => rates,
      "POST /contracts/c-1/activate": () => ({ ...draft, status: "active", allowed_actions: ["terminate"] }),
    });
    renderWithProviders(<Contracts />, { me: procurement });
    const row = await screen.findByRole("article", { name: "CT-2026-000001" });
    expect(within(row).getByText("Draft")).toBeInTheDocument();
    expect(within(row).getByText("¥5,000,000")).toBeInTheDocument();
    expect(within(row).getByText("¥1,200,000")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /contracts/c-1/activate")).toBe(true));
  });

  it("is read-only without vendor:manage", async () => {
    mockApi({ "GET /contracts": () => [draft] });
    renderWithProviders(<Contracts />);
    const row = await screen.findByRole("article", { name: "CT-2026-000001" });
    expect(within(row).queryByRole("button", { name: "Activate" })).toBeNull();
    expect(screen.getByText("Procurement officers maintain contracts.")).toBeInTheDocument();
  });

  it("offers the base currency first and the supported ones after it", async () => {
    mockApi({ "GET /contracts": () => [], "GET /vendors": () => ({ items: [], total: 0 }), "GET /fx/rates": () => rates });
    renderWithProviders(<Contracts />, { me: procurement });
    const currency = await screen.findByLabelText("Currency");
    await waitFor(() => expect(within(currency).getAllByRole("option").map((o) => o.textContent)).toEqual(["JPY", "USD", "EUR"]));
  });
});

describe("Exchange rates screen", () => {
  it("records a manual rate and explains that ECB rates are off", async () => {
    const { calls } = mockApi({
      "GET /fx/rates": () => rates,
      "PUT /fx/rates": (body) => ({ id: "r-2", ...(body as object), source: "manual", created_at: "2026-11-13T00:00:00Z" }),
    });
    renderWithProviders(<ExchangeRates />, { me: makeMe({ permissions: ["budget:manage", "budget:read"] }) });
    expect(await screen.findByText("150.25")).toBeInTheDocument();
    expect(screen.getByText(/switched off for this deployment/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fetch ECB reference rates" })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Currency"), "EUR");
    await userEvent.type(screen.getByLabelText(/^Rate/), "165.5");
    await userEvent.click(screen.getByRole("button", { name: "Save rate" }));
    await waitFor(() => expect(calls.find((c) => c.key === "PUT /fx/rates")?.body).toMatchObject({ currency: "EUR", rate: "165.5" }));
  });
});

describe("Journal export screen", () => {
  it("blocks export until the ledger accounts are set, then shows what is pending", async () => {
    mockApi({
      "GET /ledger/settings": () => ({ settings: null, mappings: [] }),
      "GET /cost-centers": () => [],
      "GET /journal-exports/pending": () => ({ configured: false, invoices: 0, payments: 0 }),
      "GET /journal-exports": () => [],
    });
    renderWithProviders(<Ledger />, { me: makeMe({ permissions: ["payment:approve"] }) });
    expect(await screen.findByText("Set the ledger accounts first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export journal" })).toBeDisabled();
    expect(screen.getByText("An organisation administrator maintains the ledger accounts.")).toBeInTheDocument();
  });

  it("explains a refused export with the server's reason", async () => {
    mockApi({
      "GET /ledger/settings": () => ({
        settings: { payable_account: "2110", input_tax_account: "1550", bank_account: "1110", default_expense_account: "6000" },
        mappings: [],
      }),
      "GET /cost-centers": () => [],
      "GET /journal-exports/pending": () => ({ configured: true, invoices: 1, payments: 0 }),
      "GET /journal-exports": () => [],
      "POST /journal-exports": () => apiError(422, "business_rule_violation", "Nothing new to export", { reason: "journal_nothing_to_export" }),
    });
    renderWithProviders(<Ledger />, { me: makeMe({ permissions: ["payment:approve"] }) });
    expect(await screen.findByText("Ready to export: 1 invoices, 0 payments.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Export journal" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
