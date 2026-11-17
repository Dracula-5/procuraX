import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { applyLanguage } from "../i18n";
import { Notifications } from "../pages/Notifications";
import { mockApi, renderWithProviders } from "../test/utils";
import { NotificationBell } from "./NotificationBell";

const routed = {
  id: "n-1",
  event_type: "approval.routed",
  payload: { number: "PR-2026-000042", title: "Laptops", amount_value: "600000", currency: "JPY", role: "manager" },
  link: "/app/requests/pr-1",
  read_at: null,
  created_at: "2026-11-12T09:00:00Z",
};

describe("NotificationBell", () => {
  it("shows the unread count and renders each event from its facts", async () => {
    mockApi({ "GET /notifications": () => ({ items: [routed], unread_count: 1 }) });
    renderWithProviders(<NotificationBell />);
    const bell = await screen.findByRole("button", { name: "Notifications: 1 unread" });
    await userEvent.click(bell);
    expect(screen.getByText("Approval needed: PR-2026-000042")).toBeInTheDocument();
    expect(screen.getByText("Laptops (¥600,000) is waiting for your approval as Manager.")).toBeInTheDocument();
  });

  it("re-renders stored notifications in Japanese", async () => {
    await applyLanguage("ja");
    mockApi({ "GET /notifications": () => ({ items: [routed], unread_count: 1 }) });
    renderWithProviders(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: "通知：未読 1 件" }));
    expect(screen.getByText("承認依頼：PR-2026-000042")).toBeInTheDocument();
    expect(screen.getByText("Laptops（￥600,000）が上長（承認者）としてのあなたの承認を待っています。")).toBeInTheDocument();
  });

  it("marks an item read when opened and can mark everything read", async () => {
    const { calls } = mockApi({
      "GET /notifications": () => ({ items: [routed], unread_count: 1 }),
      "POST /notifications/n-1/read": () => new Response(null, { status: 204 }),
      "POST /notifications/read-all": () => ({ updated: 1 }),
    });
    renderWithProviders(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: "Notifications: 1 unread" }));
    await userEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await userEvent.click(screen.getByText("Approval needed: PR-2026-000042"));
    await waitFor(() => expect(calls.map((c) => c.key)).toEqual(expect.arrayContaining(["POST /notifications/read-all", "POST /notifications/n-1/read"])));
  });
});

describe("Notification preferences", () => {
  it("saves e-mail opt-out and muted events", async () => {
    const { calls } = mockApi({
      "GET /notifications": () => ({ items: [], unread_count: 0 }),
      "GET /notifications/preferences": () => ({ email_enabled: true, muted_events: [], email_available: false, event_types: [] }),
      "PUT /notifications/preferences": (body) => ({ ...(body as object), email_available: false, event_types: [] }),
    });
    renderWithProviders(<Notifications />);
    expect(await screen.findByText(/E-mail is not set up for this deployment yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Purchase order sent"));
    await waitFor(() =>
      expect(calls.find((c) => c.key === "PUT /notifications/preferences")?.body).toEqual({ email_enabled: true, muted_events: ["purchase_order.sent"] }),
    );
  });
});
