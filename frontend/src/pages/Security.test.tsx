import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeMe, mockApi, renderWithProviders } from "../test/utils";
import { Security } from "./Security";

const config = { registration_enabled: true, demo_mode: false, oidc_enabled: false, oidc_display_name: null, mfa_available: true };
const sessions = [
  { id: "s-1", method: "password_mfa", created_at: "2026-11-12T00:00:00Z", last_used_at: "2026-11-13T00:00:00Z", expires_at: "2026-11-16T00:00:00Z", user_agent: "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0", current: true },
  { id: "s-2", method: "oidc", created_at: "2026-11-01T00:00:00Z", last_used_at: "2026-11-10T00:00:00Z", expires_at: "2026-11-15T00:00:00Z", user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1", current: false },
];

describe("Security settings", () => {
  it("sets up two-factor sign-in with a QR code and shows the recovery codes once", async () => {
    const codes = ["abcde-fghij", "klmno-pqrst"];
    const { calls } = mockApi({
      "GET /auth/config": () => config,
      "GET /auth/sessions": () => sessions,
      "POST /auth/mfa/setup": () => ({ secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/ProcuraX:pat@example.com?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", digits: 6, period: 30 }),
      "POST /auth/mfa/enable": () => ({ recovery_codes: codes }),
      "GET /auth/me": () => makeMe({ mfa_enabled: true }),
    });
    renderWithProviders(<Security />);
    await userEvent.click(await screen.findByRole("button", { name: "Set up two-factor sign-in" }));
    const qr = await screen.findByAltText("QR code for your authenticator app");
    expect(qr.getAttribute("src")).toMatch(/^data:image\/gif;base64,/);
    expect(screen.getByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Turn on" }));
    const list = await screen.findByRole("list", { name: "Your recovery codes" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(codes);
    expect(calls.find((c) => c.key === "POST /auth/mfa/enable")?.body).toEqual({ code: "123456" });
  });

  it("lists sessions and signs out the others", async () => {
    const { calls } = mockApi({
      "GET /auth/config": () => config,
      "GET /auth/sessions": () => sessions,
      "POST /auth/sessions/revoke-others": () => ({ revoked: 1 }),
    });
    renderWithProviders(<Security />, { me: makeMe({ mfa_enabled: true }) });
    expect(await screen.findByText("This browser")).toBeInTheDocument();
    expect(screen.getByText(/Chrome · Windows/)).toBeInTheDocument();
    expect(screen.getByText(/Safari · iOS/)).toBeInTheDocument();
    expect(screen.getByText(/single sign-on/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sign out" })).toHaveLength(1); // not for this browser
    await userEvent.click(screen.getByRole("button", { name: "Sign out everywhere else" }));
    expect(await screen.findByText("Signed out of 1 other session.")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.key === "POST /auth/sessions/revoke-others")).toBe(true));
    expect(screen.getByRole("button", { name: "Turn off" })).toBeInTheDocument();
  });
});
