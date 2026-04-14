"""The multipart form fields of an extraction request."""

from pydantic import BaseModel, Field

from app.features.extraction.schemas.output_mode import OutputMode

_SKILL_VERSION_PATTERN = r"^(latest|[1-9][0-9]*)$"


class ExtractRequest(BaseModel):
    """Form-field portion of ``POST /api/v1/extract``.

    The PDF binary itself is NOT a field on this model; it is a
    ``fastapi.UploadFile`` parameter on the router function per FastAPI
    multipart conventions. This model only carries the accompanying form
    fields so that they remain type-checked and round-trippable in tests.

    ``skill_version`` is a string because it accepts either a positive integer
    (``"1"``, ``"2"``, ...) or the literal ``"latest"`` alias. The service
    layer resolves the alias to a concrete integer before it reaches
    ``ExtractResponse``. The regex rejects empty strings, zero, negative
    numbers, leading zeros, decimals, and any non-``latest`` word at the
    schema boundary so callers fail fast with a 422 instead of reaching the
    skill resolver with garbage.
    """

    skill_name: str
    skill_version: str = Field(pattern=_SKILL_VERSION_PATTERN)
    output_mode: OutputMode
