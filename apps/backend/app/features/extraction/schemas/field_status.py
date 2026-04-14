"""Per-field status for an extracted field in the response."""

from enum import StrEnum


class FieldStatus(StrEnum):
    """Whether the extractor produced a value for a declared field."""

    extracted = "extracted"
    failed = "failed"
