import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { WidgetForm } from "@/features/widgets/components/widget-form/widget-form";
import { WidgetList } from "@/features/widgets/components/widget-list/widget-list";

export const Route = createFileRoute("/$lang/widgets/")({
  component: WidgetsPage,
});

function WidgetsPage() {
  const { t } = useTranslation();
  return (
    <div>
      <h2 className="mb-4 text-xl font-bold">{t("widgets.title")}</h2>
      <WidgetForm />
      <WidgetList />
    </div>
  );
}
