"""Translation validator: checks that all locale files match the error contracts.

Validates:
1. Every required key exists in every locale
2. No extra keys in any locale
3. Every contract param is referenced in the translation string
4. No undefined params referenced in translation strings
5. No empty translation strings
"""

import json
import re
from pathlib import Path


def _extract_params(translation: str) -> set[str]:
    """Extract {{param}} references from a translation string."""
    return set(re.findall(r"\{\{(\w+)\}\}", translation))


def validate(required_keys_path: Path, locales_dir: Path) -> list[str]:
    """Validate all locale files against the required keys contract.

    Returns a list of error messages. Empty list = all valid.
    """
    contract = json.loads(required_keys_path.read_text())
    required_keys = set(contract["keys"])
    params_by_key: dict[str, list[str]] = contract["params_by_key"]
    errors: list[str] = []

    # Find all locale directories
    locale_dirs = [d for d in locales_dir.iterdir() if d.is_dir()]

    if not locale_dirs:
        errors.append("No locale directories found")
        return errors

    for locale_dir in sorted(locale_dirs):
        lang = locale_dir.name
        errors_file = locale_dir / "errors.json"

        if not errors_file.exists():
            errors.append(f"[{lang}] Missing errors.json")
            continue

        translations = json.loads(errors_file.read_text())
        translation_keys = set(translations.keys())

        # Check 1: missing keys
        missing = required_keys - translation_keys
        for key in sorted(missing):
            errors.append(f"[{lang}] Missing key: {key}")

        # Check 2: extra keys
        extra = translation_keys - required_keys
        for key in sorted(extra):
            errors.append(f"[{lang}] Extra key not in contract: {key}")

        # Check 3 & 4: param validation
        for key in required_keys & translation_keys:
            translation = translations[key]

            # Check 5: empty strings
            if not translation or not translation.strip():
                errors.append(f"[{lang}] Empty translation for key: {key}")
                continue

            referenced_params = _extract_params(translation)
            required_params = set(params_by_key.get(key, []))

            # Check 3: missing required params
            missing_params = required_params - referenced_params
            for param in sorted(missing_params):
                errors.append(f"[{lang}] Key '{key}' missing required param: {param}")

            # Check 4: undefined params
            undefined_params = referenced_params - required_params
            for param in sorted(undefined_params):
                errors.append(
                    f"[{lang}] Key '{key}' references undefined param: {param}"
                )

    return errors
