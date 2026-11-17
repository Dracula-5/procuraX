import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTranslation } from "react-i18next";
import { describe, expect, it } from "vitest";
import i18n from "../i18n";
import { makeMe, mockApi, renderWithProviders } from "../test/utils";
import { LanguageSwitcher } from "./LanguageSwitcher";

function Page() {
  const { t } = useTranslation();
  return (
    <>
      <LanguageSwitcher />
      <h1>{t("nav.item.requests")}</h1>
    </>
  );
}

describe("LanguageSwitcher", () => {
  it("switches the UI and saves the choice to the signed-in user's profile", async () => {
    const me = makeMe();
    const { calls } = mockApi({ "PATCH /auth/me/preferences": (body) => ({ ...me, ...(body as object) }) });
    const { auth } = renderWithProviders(<Page />, { me });
    expect(screen.getByRole("heading")).toHaveTextContent("Purchase requests");

    await userEvent.selectOptions(screen.getByLabelText("Language"), "ja");

    await waitFor(() => expect(screen.getByRole("heading")).toHaveTextContent("購買申請"));
    expect(i18n.language).toBe("ja");
    expect(document.documentElement.lang).toBe("ja");
    expect(localStorage.getItem("procurax.lang")).toBe("ja");
    await waitFor(() => expect(calls).toEqual([{ key: "PATCH /auth/me/preferences", body: { locale: "ja" } }]));
    await waitFor(() => expect(auth.updateMe).toHaveBeenCalledWith(expect.objectContaining({ locale: "ja" })));
  });

  it("only remembers the choice on the device before sign-in", async () => {
    const { calls } = mockApi({});
    renderWithProviders(<Page />, { me: null });
    await userEvent.selectOptions(screen.getByLabelText("Language"), "ja");
    await waitFor(() => expect(screen.getByRole("heading")).toHaveTextContent("購買申請"));
    expect(localStorage.getItem("procurax.lang")).toBe("ja");
    expect(calls).toEqual([]);
  });
});
