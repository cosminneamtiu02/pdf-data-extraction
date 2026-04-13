import { useTranslation } from "react-i18next";
import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, type SupportedLanguage } from "@/i18n/config";

export function useCurrentLanguage(): SupportedLanguage {
  const { i18n } = useTranslation();
  const lang = i18n.language;

  // i18n.language can return sub-locales like "en-US". Validate before casting.
  if (SUPPORTED_LANGUAGES.includes(lang as SupportedLanguage)) {
    return lang as SupportedLanguage;
  }

  // Try the base language (e.g. "en-US" -> "en")
  const base = lang.split("-")[0];
  if (base && SUPPORTED_LANGUAGES.includes(base as SupportedLanguage)) {
    return base as SupportedLanguage;
  }

  return DEFAULT_LANGUAGE;
}
