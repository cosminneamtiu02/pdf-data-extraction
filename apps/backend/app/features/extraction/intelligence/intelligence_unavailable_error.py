"""IntelligenceUnavailableError: Python-internal placeholder exception.

The concrete `OllamaGemmaProvider` raises this when Ollama cannot be reached,
times out, or returns a server error. It is a Python-level placeholder only —
PDFX-E004-F004 will add an `INTELLIGENCE_UNAVAILABLE` entry to `errors.yaml`
and a typed `DomainError` wrapper that the API middleware can map to a 503
response envelope. Until that feature lands, this exception lives inside the
intelligence feature and never crosses the HTTP boundary.
"""

from __future__ import annotations


class IntelligenceUnavailableError(Exception):
    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause
