"""Per-request extraction metadata returned alongside the fields."""

from pydantic import BaseModel


class ExtractionMetadata(BaseModel):
    """Observability fields attached to every extraction response.

    ``attempts_per_field`` maps each declared field name to the number of
    LLM attempts the extractor spent on it (1-based; always >= 1 for fields
    that were attempted). ``parser_warnings`` carries heterogeneous warnings
    surfaced by the document parser as plain strings; richer modelling is
    intentionally deferred.
    """

    page_count: int
    duration_ms: int
    attempts_per_field: dict[str, int]
    parser_warnings: list[str] = []
