/**
 * MoneyDisplay — locale-aware money formatting.
 * All money rendering goes through this component.
 */
import { useTranslation } from "react-i18next";
import { formatCurrency } from "@/shared/lib/format";

interface MoneyDisplayProps {
  amountMinor: number;
  currency: string;
  className?: string | undefined;
}

export function MoneyDisplay({ amountMinor, currency, className }: MoneyDisplayProps) {
  const { i18n } = useTranslation();

  return <span className={className}>{formatCurrency(amountMinor, currency, i18n.language)}</span>;
}
