import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enCommon from "./locales/en/common.json";
import enErrors from "./locales/en/errors.json";
import roCommon from "./locales/ro/common.json";
import roErrors from "./locales/ro/errors.json";

export const SUPPORTED_LANGUAGES = ["en", "ro"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];
export const DEFAULT_LANGUAGE: SupportedLanguage = "en";

const resources = {
  en: { common: enCommon, errors: enErrors },
  ro: { common: roCommon, errors: roErrors },
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: DEFAULT_LANGUAGE,
    supportedLngs: SUPPORTED_LANGUAGES,
    defaultNS: "common",
    ns: ["common", "errors"],
    interpolation: {
      escapeValue: false, // React already escapes
    },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "app.language",
      caches: ["localStorage"],
    },
  });

export default i18n;
