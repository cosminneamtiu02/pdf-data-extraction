import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders, setTestLanguage } from "@/test-utils";
import { DateTime } from "./date-time";

describe("DateTime", () => {
  it("renders timestamp in user locale", async () => {
    await setTestLanguage("en");
    renderWithProviders(<DateTime value="2026-01-15T10:30:00Z" />);

    // Should render a formatted date — exact format depends on Intl
    const el = screen.getByText(/2026|Jan/);
    expect(el).toBeInTheDocument();
  });

  it("renders timestamp in Romanian locale", async () => {
    await setTestLanguage("ro");
    renderWithProviders(<DateTime value="2026-01-15T10:30:00Z" />);

    const el = screen.getByText(/2026|ian/i);
    expect(el).toBeInTheDocument();
    await setTestLanguage("en");
  });
});
