/**
 * Money wrapper — uses integer minor units (cents) to avoid float precision issues.
 * Frontend money formatting uses Intl.NumberFormat via format.ts.
 *
 * Note: dinero.js is available for complex arithmetic but for the template
 * we use a simple wrapper since Widget doesn't use money.
 */

export interface Money {
  amount: number; // minor units (e.g. cents)
  currency: string; // ISO 4217 code
}

export function createMoney(amountMinor: number, currency: string): Money {
  if (!Number.isInteger(amountMinor)) {
    throw new Error("amount must be an integer (minor units)");
  }
  return { amount: amountMinor, currency };
}

export function formatMoney(money: Money, locale: string): string {
  const major = money.amount / 100;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: money.currency,
  }).format(major);
}
