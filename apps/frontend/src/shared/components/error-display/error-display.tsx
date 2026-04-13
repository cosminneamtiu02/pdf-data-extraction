import { ErrorMessage } from "@/shared/components/error-message/error-message";
/**
 * ErrorDisplay — wraps ErrorMessage in a larger display container.
 * Use for full-page or section-level error displays.
 */
import type { ApiError } from "@/shared/lib/api-client";

interface ErrorDisplayProps {
  error: ApiError;
}

export function ErrorDisplay({ error }: ErrorDisplayProps) {
  return (
    <div className="mx-auto max-w-md py-8">
      <ErrorMessage error={error} />
    </div>
  );
}
