"""Output-mode enum for the extraction endpoint."""

from enum import StrEnum


class OutputMode(StrEnum):
    """Selects which artifact(s) the extraction endpoint returns.

    - ``JSON_ONLY``: only the structured JSON response body.
    - ``PDF_ONLY``: only the annotated PDF bytes.
    - ``BOTH``: a multipart/mixed response containing both.
    """

    JSON_ONLY = "JSON_ONLY"
    PDF_ONLY = "PDF_ONLY"
    BOTH = "BOTH"
