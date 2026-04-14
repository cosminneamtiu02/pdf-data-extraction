"""The structured JSON response body of an extraction request."""

from pydantic import BaseModel

from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata


class ExtractResponse(BaseModel):
    """The JSON artifact returned for ``output_mode=JSON_ONLY`` or ``BOTH``.

    ``skill_version`` is an integer because the service has already resolved
    any ``"latest"`` alias from the request to a concrete version number by
    the time the response is assembled; downstream consumers can therefore
    always rely on an integer.

    ``fields`` is keyed by the declared field name. The "every declared field
    always present" API stability invariant is enforced upstream in the
    extraction service, not by this schema — at the schema layer an empty
    mapping is legal.
    """

    skill_name: str
    skill_version: int
    fields: dict[str, ExtractedField]
    metadata: ExtractionMetadata
