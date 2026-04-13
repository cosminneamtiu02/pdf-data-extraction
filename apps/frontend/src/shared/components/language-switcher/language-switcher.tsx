/**
 * LanguageSwitcher — lists supported languages and persists choice.
 * Changes the i18n language. When used inside the router, the $lang
 * route's beforeLoad will sync the URL on the next navigation.
 */
import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES } from "@/i18n/config";

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation();

  function handleSwitch(lang: string) {
    i18n.changeLanguage(lang);
  }

  return (
    <div className="flex gap-2">
      {SUPPORTED_LANGUAGES.map((lang) => (
        <button
          key={lang}
          type="button"
          onClick={() => handleSwitch(lang)}
          className={`rounded px-2 py-1 text-sm ${
            i18n.language === lang
              ? "bg-blue-600 text-white"
              : "bg-gray-100 text-gray-700 hover:bg-gray-200"
          }`}
        >
          {t(`language.${lang}`)}
        </button>
      ))}
    </div>
  );
}
