import { describe, expect, it } from "vitest";
import { formatCurrency, formatDate, formatNumber } from "./format";

describe("formatNumber", () => {
  it("formats with en-US locale (comma thousands)", () => {
    expect(formatNumber(1234567, "en-US")).toBe("1,234,567");
  });

  it("formats with de-DE locale (period thousands)", () => {
    expect(formatNumber(1234567, "de-DE")).toBe("1.234.567");
  });
});

describe("formatDate", () => {
  it("formats ISO timestamp in en-US locale", () => {
    const result = formatDate("2026-03-15T14:30:00Z", "en-US");
    // Don't assert exact format — it's TZ-dependent — just assert the month is present.
    expect(result).toMatch(/Mar/);
  });
});

describe("formatCurrency", () => {
  it("divides minor units by 100", () => {
    const formatted = formatCurrency(1050, "USD", "en-US");
    expect(formatted).toContain("10.50");
    expect(formatted).toContain("$");
  });
});
