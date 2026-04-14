"""Per-skill Docling parser overrides.

This module MUST NOT import the `docling` package. It holds plain configuration
values — applying them to a real Docling object is the parser layer's job
(PDFX-E003-F003). Import-linter contracts in PDFX-E007-F004 enforce this.
"""

from pydantic import BaseModel, ConfigDict


class SkillDoclingConfig(BaseModel):
    """Closed-shape Docling override block. All fields optional.

    The exact named fields are intentionally minimal here; richer per-field
    validation against Docling's actual configuration surface lives in the
    parsing layer (PDFX-E003-F003).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ocr: str | None = None
    table_mode: str | None = None
