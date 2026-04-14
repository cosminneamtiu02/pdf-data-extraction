"""DoclingConfig: merged effective Docling pipeline knobs consumed by the parser.

Distinct from `SkillDoclingConfig` (PDFX-E002-F001), which holds the per-skill
override before merging. The merge step lives in PDFX-E003-F003. This type is
the parser-layer runtime shape and is intentionally minimal — fields are added
only when PDFX-E003-F002 actually needs to pass them to Docling.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DoclingConfig:
    ocr: str
    table_mode: str
