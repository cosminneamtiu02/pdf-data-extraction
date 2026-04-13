/**
 * WidgetList — displays a paginated list of widgets.
 * Fetches data via useWidgets(). Renders four states: loading, error, empty, data.
 */
import { useTranslation } from "react-i18next";
import { useWidgets } from "@/features/widgets/api/use-widgets";
import { DateTime } from "@/shared/components/date-time/date-time";
import { ErrorMessage } from "@/shared/components/error-message/error-message";
import { ApiError } from "@/shared/lib/api-client";

export function WidgetList() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error } = useWidgets();

  if (isLoading) {
    return (
      <div className="py-4 text-center text-gray-500">{t("common:loading", "Loading...")}</div>
    );
  }

  if (isError && error) {
    if (error instanceof ApiError) {
      return <ErrorMessage error={error} />;
    }
    return (
      <div
        role="alert"
        className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800"
      >
        {t("common:network_error", "A network error occurred. Please try again.")}
      </div>
    );
  }

  if (!data || data.items.length === 0) {
    return <div className="py-8 text-center text-gray-500">{t("widgets.empty")}</div>;
  }

  return (
    <div className="space-y-3">
      {data.items.map((widget) => (
        <div key={widget.id} className="rounded-lg border bg-white p-4 shadow-sm">
          <h3 className="font-medium">{widget.name}</h3>
          {widget.description && <p className="mt-1 text-sm text-gray-600">{widget.description}</p>}
          <div className="mt-2 text-xs text-gray-400">
            {t("widgets.created_at")}: <DateTime value={widget.created_at} />
          </div>
        </div>
      ))}
    </div>
  );
}
