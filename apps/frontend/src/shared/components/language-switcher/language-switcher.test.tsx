import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithProviders, setTestLanguage, testI18n } from "@/test-utils";
import { LanguageSwitcher } from "./language-switcher";

describe("LanguageSwitcher", () => {
  it("lists supported languages", async () => {
    await setTestLanguage("en");
    renderWithProviders(<LanguageSwitcher />);

    expect(screen.getByText("English")).toBeInTheDocument();
    expect(screen.getByText("Romanian")).toBeInTheDocument();
  });

  it("changes i18n language on click", async () => {
    await setTestLanguage("en");
    const user = userEvent.setup();
    renderWithProviders(<LanguageSwitcher />);

    const roButton = screen.getByText("Romanian");
    await user.click(roButton);

    // Verify i18n language changed
    expect(testI18n.language).toBe("ro");

    // Cleanup
    await setTestLanguage("en");
  });
});
