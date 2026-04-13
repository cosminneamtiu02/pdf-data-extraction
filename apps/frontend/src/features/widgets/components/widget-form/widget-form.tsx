/**
 * WidgetForm — controlled form for creating a new widget.
 * Client-side validation: name is required.
 */
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useCreateWidget } from "@/features/widgets/api/use-create-widget";
import { ErrorMessage } from "@/shared/components/error-message/error-message";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { ApiError } from "@/shared/lib/api-client";

export function WidgetForm() {
  const { t } = useTranslation();
  const createWidget = useCreateWidget();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;

    createWidget.mutate(
      { name: name.trim(), description: description.trim() || null },
      {
        onSuccess: () => {
          setName("");
          setDescription("");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="mb-6 space-y-3 rounded-lg border bg-white p-4">
      <div>
        <label htmlFor="widget-name" className="block text-sm font-medium">
          {t("widgets.name")}
        </label>
        <Input
          id="widget-name"
          type="text"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("widgets.name_placeholder")}
          className="mt-1"
        />
      </div>

      <div>
        <label htmlFor="widget-description" className="block text-sm font-medium">
          {t("widgets.description")}
        </label>
        <Input
          id="widget-description"
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("widgets.description_placeholder")}
          className="mt-1"
        />
      </div>

      {createWidget.isError &&
        createWidget.error &&
        (createWidget.error instanceof ApiError ? (
          <ErrorMessage error={createWidget.error} />
        ) : (
          <div
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800"
          >
            {t("common:network_error", "A network error occurred. Please try again.")}
          </div>
        ))}

      <Button type="submit" disabled={createWidget.isPending}>
        {t("widgets.create")}
      </Button>
    </form>
  );
}
