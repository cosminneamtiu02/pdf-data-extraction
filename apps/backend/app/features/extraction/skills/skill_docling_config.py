"""Per-skill Docling parser overrides.

This module MUST NOT import the `docling` package. It holds plain configuration
values — applying them to a real Docling object is the parser layer's job
(PDFX-E003-F003). Import-linter contracts in PDFX-E007-F004 enforce this.
"""

from pydantic import BaseModel, ConfigDict

from app.core.docling_modes import OcrMode, TableMode


class SkillDoclingConfig(BaseModel):
    """Closed-shape Docling override block. All fields optional.

    Values are validated at construction time against the same vocabulary the
    parser layer enforces in `DoclingConfig` (PDFX-E003-F003), so a typo in a
    skill YAML fails at `SkillYamlSchema.load_from_file` instead of leaking
    through to runtime Docling configuration drift.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ocr: OcrMode | None = None
    table_mode: TableMode | None = None
