import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { delegationState } from "../lib/delegations";
import { makeMe, mockApi, renderWithProviders } from "../test/utils";
import { Delegations } from "./Delegations";

const step = {
  approval_id: "a-1",
  purchase_request_id: "pr-1",
  number: "PR-2026-000042",
  title: "Laptops",
  requester_name: "Emi Employee",
  approver_role: "manager",
  status: "pending",
  estimated_total: "600000",
  currency: "JPY",
  due_at: null,
};
const given = {
  id: "d-1",
  approval_id: "a-9",
  purchase_request_id: "pr-9",
  request_number: "PR-2026-000041",
  request_title: "Desks",
  approver_role: "manager",
  step_status: "pending",
  delegator_id: "u-1",
  delegator_name: "Pat Approver",
  delegate_id: "u-2",
  delegate_name: "Bo Backup",
  delegated_at: "2026-11-10T09:00:00Z",
  revoked_at: null,
  can_revoke: true,
};

describe("Delegations screen", () => {
  it("delegates a step to a colleague the API says is eligible", async () => {
    const { calls } = mockApi({
      "GET /approvals/delegable": () => [step],
      "GET /approvals/delegations": () => [],
      "GET /approvals/a-1/delegate-candidates": () => [{ id: "u-2", full_name: "Bo Backup", job_title: "Deputy manager" }],
      "POST /approvals/delegations": () => ({ id: "d-2" }),
    });
    renderWithProviders(<Delegations />);
    expect(await screen.findByText("PR-2026-000042 · Laptops")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Choose delegate" }));
    const select = await screen.findByLabelText("Delegate PR-2026-000042 to");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["— Choose a colleague —", "Bo Backup · Deputy manager"]);
    const delegate = screen.getByRole("button", { name: "Delegate" });
    expect(delegate).toBeDisabled();
    await userEvent.selectOptions(select, "u-2");
    await userEvent.click(delegate);
    await waitFor(() => expect(calls.some((c) => c.key === "POST /approvals/delegations")).toBe(true));
    expect(calls.find((c) => c.key === "POST /approvals/delegations")?.body).toEqual({ approval_id: "a-1", delegate_user_id: "u-2" });
  });

  it("lists delegations and offers revoke only where the API allows it", async () => {
    const received = { ...given, id: "d-3", delegator_id: "u-5", delegator_name: "Kai Head", delegate_id: "u-1", delegate_name: "Pat Approver", can_revoke: false };
    const { calls } = mockApi({
      "GET /approvals/delegable": () => [],
      "GET /approvals/delegations": () => [given, received],
      "POST /approvals/delegations/d-1/revoke": () => ({ id: "d-1" }),
    });
    renderWithProviders(<Delegations />);
    expect(await screen.findByText("No steps to delegate")).toBeInTheDocument();
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3); // header + 2
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /approvals/delegations/d-1/revoke")).toBe(true));
  });

  it("derives the display state", () => {
    expect(delegationState({ revoked_at: null, step_status: "waiting" })).toBe("active");
    expect(delegationState({ revoked_at: "2026-11-10T00:00:00Z", step_status: "pending" })).toBe("revoked");
    expect(delegationState({ revoked_at: null, step_status: "approved" })).toBe("completed");
  });

  it("is not reachable without the approver permission", async () => {
    const { RequirePermission } = await import("../App");
    mockApi({});
    renderWithProviders(
      <RequirePermission permission="approval:act">
        <Delegations />
      </RequirePermission>,
      { me: makeMe({ permissions: ["purchase_request:create"] }) },
    );
    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
  });
});
