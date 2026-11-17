import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderWithProviders } from "../test/utils";
import { AdvisoryFlags, SqlAnalysis } from "./AiPanels";

describe("AI panels", () => {
  it("shows advisory flags as review notes that do not change routing", async () => {
    mockApi({
      "GET /purchase-requests/pr-1/advisories": () => [
        {
          prediction_id: "p-1",
          model: "procurax-advisors",
          model_version: "1",
          created_at: "2026-11-13T00:00:00Z",
          submission: 1,
          flags: [
            { code: "amount_outlier", severity: "high", explanation: "Amount is 20.0x the median." },
            { code: "possible_duplicate", severity: "info", related_request: "PR-2026-000041" },
          ],
        },
      ],
    });
    renderWithProviders(<AdvisoryFlags requestId="pr-1" />);
    expect(await screen.findByText("Unusual amount for this category")).toBeInTheDocument();
    expect(screen.getByText("Possible duplicate request")).toBeInTheDocument();
    expect(screen.getByText("PR-2026-000041")).toBeInTheDocument();
    expect(screen.getByText(/do not change the approval route/)).toBeInTheDocument();
  });

  it("renders nothing when no advisory was recorded", async () => {
    const { calls } = mockApi({ "GET /purchase-requests/pr-2/advisories": () => [] });
    const { container } = renderWithProviders(<AdvisoryFlags requestId="pr-2" />);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the guarded SQL, its rows, and refusals with the reason", async () => {
    let answer: object = {
      status: "answered",
      sql: "SELECT category, sum(base_amount) AS spend FROM purchase_requests GROUP BY category LIMIT 200",
      columns: ["category", "spend"],
      rows: [["software", "1200000"]],
      reason: null,
      model: "claude-opus-5-5",
    };
    mockApi({ "POST /procurement-assistant/sql-query": () => answer });
    renderWithProviders(<SqlAnalysis />);
    await userEvent.type(screen.getByRole("textbox"), "Spend by category");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("1200000")).toBeInTheDocument();
    expect(screen.getByText(/LIMIT 200/)).toBeInTheDocument();
    expect(screen.getByText(/checked by the SQL guard/)).toBeInTheDocument();

    answer = { status: "refused", sql: "SELECT set_config('app.rls_bypass', 'on', true)", columns: [], rows: [], reason: "sql_function", model: "claude-opus-5-5" };
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Refused by the SQL guard")).toBeInTheDocument();
    expect(screen.getByText("The query used a function that is not allowed.")).toBeInTheDocument();
  });
});
