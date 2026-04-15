"""Single source of truth for Docling pipeline knob vocabularies.

Defined in `app.core` so `Settings` (core), `SkillDoclingConfig` (features,
skill layer) and `DoclingConfig` (features, parser layer) can all reference
the same `Literal` aliases without `core` having to import from `features` —
the `shared-no-features` import-linter contract forbids that direction.

Every consumer is closed-shape on these aliases:

- `Settings.docling_ocr_default` / `docling_table_mode_default` reject bad
  values at process start via pydantic-settings.
- `SkillDoclingConfig` rejects bad values at `SkillYamlSchema.load_from_file`
  time via pydantic.
- `DoclingConfig` (parser layer) both types its fields on these aliases and
  derives its runtime `_VALID_*` sets from `typing.get_args(...)`, so adding
  a new mode here propagates everywhere without a second edit.
"""

from typing import Literal

OcrMode = Literal["auto", "force", "off"]
TableMode = Literal["fast", "accurate"]
