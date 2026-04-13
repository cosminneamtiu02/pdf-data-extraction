/**
 * ErrorMessage — THE error renderer. All errors displayed to users go through this.
 * Uses the `errors` i18n namespace to translate error codes.
 */
import { useTranslation } from "react-i18next";
import type { ApiError } from "@/shared/lib/api-client";

interface ErrorMessageProps {
  error: ApiError;
}

export function ErrorMessage({ error }: ErrorMessageProps) {
  const { t } = useTranslation("errors");
  const { t: tCommon } = useTranslation("common");

  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-4">
      <p className="text-sm text-red-800">
        {t(error.code, error.params as Record<string, unknown>)}
      </p>
      <p className="mt-1 text-xs text-red-500">{tCommon("request_id", { id: error.requestId })}</p>
    </div>
  );
}
