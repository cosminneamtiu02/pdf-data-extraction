import { describe, expect, it } from "vitest";
import { createMoney, formatMoney } from "./money";

describe("createMoney", () => {
  it("creates from minor units", () => {
    const money = createMoney(1050, "USD");
    expect(money.amount).toBe(1050);
    expect(money.currency).toBe("USD");
  });

  it("throws on non-integer amount", () => {
    expect(() => createMoney(10.5, "USD")).toThrow("amount must be an integer");
  });
});

describe("formatMoney", () => {
  it("formats USD in en-US locale", () => {
    const money = createMoney(1050, "USD");
    // Intl.NumberFormat uses NBSP (U+00A0) between symbol/amount in some locales,
    // so assert on the digit+currency substring rather than exact spacing.
    const formatted = formatMoney(money, "en-US");
    expect(formatted).toContain("10.50");
    expect(formatted).toContain("$");
  });

  it("formats EUR in de-DE locale", () => {
    const money = createMoney(2575, "EUR");
    const formatted = formatMoney(money, "de-DE");
    expect(formatted).toContain("25,75");
    expect(formatted).toContain("€");
  });
});
