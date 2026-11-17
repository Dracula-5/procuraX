import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RequirePermission } from "../App";
import { applyLanguage } from "../i18n";
import { makeMe, renderWithProviders } from "../test/utils";

describe("RequirePermission (UX guard; the API enforces independently)", () => {
  it("shows the page when the user holds the permission", () => {
    renderWithProviders(<RequirePermission permission="approval:act">secret page</RequirePermission>);
    expect(screen.getByText("secret page")).toBeInTheDocument();
  });

  it("shows a translated no-access message otherwise", async () => {
    const me = makeMe({ permissions: ["purchase_request:create"] });
    const { unmount } = renderWithProviders(<RequirePermission permission="approval:act">secret page</RequirePermission>, { me });
    expect(screen.queryByText("secret page")).not.toBeInTheDocument();
    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
    unmount();
    await applyLanguage("ja");
    renderWithProviders(<RequirePermission permission="approval:act">secret page</RequirePermission>, { me });
    expect(screen.getByText("このページへのアクセス権がありません")).toBeInTheDocument();
  });

  it("accepts any one of several permissions", () => {
    const finance = makeMe({ permissions: ["payment:approve"] });
    renderWithProviders(<RequirePermission permission={["organization:manage", "payment:approve"]}>settings</RequirePermission>, { me: finance });
    expect(screen.getByText("settings")).toBeInTheDocument();
  });
});
