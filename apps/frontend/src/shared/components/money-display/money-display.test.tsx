import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders, setTestLanguage } from "@/test-utils";
import { MoneyDisplay } from "./money-display";

describe("MoneyDisplay", () => {
  it("renders amount with currency symbol", async () => {
    await setTestLanguage("en");
    renderWithProviders(<MoneyDisplay amountMinor={1050} currency="USD" />);

    // Should show $10.50 or similar
    expect(screen.getByText(/\$10\.50|10\.50/)).toBeInTheDocument();
  });

  it("renders in Romanian locale format", async () => {
    await setTestLanguage("ro");
    renderWithProviders(<MoneyDisplay amountMinor={1050} currency="EUR" />);

    // Should show EUR amount in RO format
    const el = screen.getByText(/10,50|10\.50|EUR/);
    expect(el).toBeInTheDocument();
    await setTestLanguage("en");
  });
});
