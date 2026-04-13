import { createFileRoute, Navigate } from "@tanstack/react-router";
import { DEFAULT_LANGUAGE } from "@/i18n/config";

export const Route = createFileRoute("/")({
  component: IndexRedirect,
});

function IndexRedirect() {
  return <Navigate to="/$lang/widgets" params={{ lang: DEFAULT_LANGUAGE }} replace />;
}
