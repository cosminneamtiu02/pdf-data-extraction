/**
 * DateTime — locale-aware timestamp display.
 * All timestamp rendering goes through this component.
 */
import { useTranslation } from "react-i18next";
import { formatDate } from "@/shared/lib/format";

interface DateTimeProps {
  value: string; // ISO 8601 timestamp
  className?: string | undefined;
}

export function DateTime({ value, className }: DateTimeProps) {
  const { i18n } = useTranslation();

  return (
    <time dateTime={value} className={className}>
      {formatDate(value, i18n.language)}
    </time>
  );
}
