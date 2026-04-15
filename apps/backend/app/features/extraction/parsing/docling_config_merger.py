"""Per-skill Docling config merge step (PDFX-E003-F003).

Pure, total function: overlay the optional `SkillDoclingConfig` from a skill's
YAML on top of the global defaults from `Settings`, producing the effective
`DoclingConfig` the parser layer will consume.

Field-by-field override only. No nested merging, no conditional logic beyond
"override wins if set, otherwise default." The merger never imports the
`docling` package — it traffics in feature-owned value types only.
"""

from app.core.config import Settings
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.skills.skill_docling_config import SkillDoclingConfig


def merge_docling_config(
    global_settings: Settings,
    skill_override: SkillDoclingConfig | None,
) -> DoclingConfig:
    """Return the effective Docling config for a single extraction call."""
    if skill_override is None:
        return DoclingConfig(
            ocr=global_settings.docling_ocr_default,
            table_mode=global_settings.docling_table_mode_default,
        )

    # Explicit `is not None` rather than truthiness: future Literal values
    # (e.g. an empty string sentinel) would be silently dropped by `or`.
    ocr = (
        skill_override.ocr
        if skill_override.ocr is not None
        else global_settings.docling_ocr_default
    )
    table_mode = (
        skill_override.table_mode
        if skill_override.table_mode is not None
        else global_settings.docling_table_mode_default
    )
    return DoclingConfig(ocr=ocr, table_mode=table_mode)
