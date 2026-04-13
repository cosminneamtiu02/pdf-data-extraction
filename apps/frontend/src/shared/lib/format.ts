/**
 * Locale-aware date and number formatting wrappers.
 * All timestamp display goes through these functions.
 */

export function formatDate(isoString: string, locale: string): string {
  const date = new Date(isoString);
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatNumber(value: number, locale: string): string {
  return new Intl.NumberFormat(locale).format(value);
}

/**
 * Format minor units as currency. Assumes 2 decimal places (e.g. cents for USD/EUR).
 * WARNING: Incorrect for currencies with 0 decimals (JPY, KRW) or 3 decimals (KWD, OMR).
 * When a project uses non-2-decimal currencies, add a subunit lookup table.
 */
export function formatCurrency(amountMinor: number, currency: string, locale: string): string {
  const amount = amountMinor / 100;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
  }).format(amount);
}
