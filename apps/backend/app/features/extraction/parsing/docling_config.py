"""DoclingConfig: merged effective Docling pipeline knobs consumed by the parser.

Distinct from `SkillDoclingConfig` (PDFX-E002-F001), which holds the per-skill
override before merging. The merge step lives in PDFX-E003-F003. This type is
the parser-layer runtime shape and is intentionally minimal — fields are added
only when PDFX-E003-F002 actually needs to pass them to Docling.
"""

from dataclasses import dataclass
from typing import get_args

from app.core.docling_modes import OcrMode, TableMode

_VALID_OCR_MODES: frozenset[str] = frozenset(get_args(OcrMode))
_VALID_TABLE_MODES: frozenset[str] = frozenset(get_args(TableMode))


@dataclass(frozen=True)
class DoclingConfig:
    ocr: OcrMode
    table_mode: TableMode

    def __post_init__(self) -> None:
        # Fail fast on typos. `_real_docling_converter_adapter.default_converter_factory`
        # otherwise silently maps anything that is not "off" to OCR-on and
        # anything that is not "accurate" to the FAST table-structure mode,
        # which turns configuration typos (e.g. ocr="froce") into silent
        # semantic drift instead of a visible error.
        if self.ocr not in _VALID_OCR_MODES:
            msg = f"DoclingConfig.ocr must be one of {sorted(_VALID_OCR_MODES)}, got {self.ocr!r}"
            raise ValueError(msg)
        if self.table_mode not in _VALID_TABLE_MODES:
            msg = (
                f"DoclingConfig.table_mode must be one of "
                f"{sorted(_VALID_TABLE_MODES)}, got {self.table_mode!r}"
            )
            raise ValueError(msg)
