/**
 * Shared test utilities for rendering components with providers.
 * Includes all providers a component might need: i18n, QueryClient.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type RenderOptions, render } from "@testing-library/react";
import i18n from "i18next";
import type { ReactElement } from "react";
import { I18nextProvider, initReactI18next } from "react-i18next";

import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES } from "@/i18n/config";
import enCommon from "@/i18n/locales/en/common.json";
import enErrors from "@/i18n/locales/en/errors.json";
import roCommon from "@/i18n/locales/ro/common.json";
import roErrors from "@/i18n/locales/ro/errors.json";

// Test-specific i18n instance (separate from app instance)
const testI18n = i18n.createInstance();
testI18n.use(initReactI18next).init({
  resources: {
    en: { common: enCommon, errors: enErrors },
    ro: { common: roCommon, errors: roErrors },
  },
  lng: DEFAULT_LANGUAGE,
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: [...SUPPORTED_LANGUAGES],
  defaultNS: "common",
  ns: ["common", "errors"],
  interpolation: { escapeValue: false },
});

// Test-specific QueryClient (no retries, no gc)
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function AllProviders({ children }: { children: React.ReactNode }) {
  const queryClient = createTestQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={testI18n}>{children}</I18nextProvider>
    </QueryClientProvider>
  );
}

export function renderWithProviders(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
  return render(ui, { wrapper: AllProviders, ...options });
}

export async function setTestLanguage(lang: string) {
  await testI18n.changeLanguage(lang);
}

export { testI18n };
