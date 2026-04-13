import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import type { SupportedLanguage } from "@/i18n/config";
import i18n, { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES } from "@/i18n/config";

export const Route = createFileRoute("/$lang")({
  beforeLoad: ({ params }) => {
    const lang = params.lang as string;
    if (!SUPPORTED_LANGUAGES.includes(lang as SupportedLanguage)) {
      throw redirect({ to: "/$lang/widgets", params: { lang: DEFAULT_LANGUAGE } });
    }
    if (i18n.language !== lang) {
      i18n.changeLanguage(lang);
    }
  },
  component: LangLayout,
});

function LangLayout() {
  return <Outlet />;
}
