"""StructuredOutputError: internal exception raised after retry exhaustion.

This is a Python-level exception raised by `StructuredOutputValidator` when all
retries fail. The provider's `generate` method catches it and re-raises it as
the public `STRUCTURED_OUTPUT_FAILED` DomainError subclass — that promotion
lives in PDFX-E004-F004. This exception must NEVER escape an
`IntelligenceProvider.generate` call without being wrapped.
"""


class StructuredOutputError(Exception):
    def __init__(
        self,
        message: str,
        *,
        last_raw_output: str,
        failure_reasons: list[str],
        attempts: int,
    ) -> None:
        super().__init__(message)
        self.last_raw_output = last_raw_output
        self.failure_reasons = failure_reasons
        self.attempts = attempts

    @property
    def details(self) -> dict[str, object]:
        return {
            "last_raw_output": self.last_raw_output,
            "failure_reasons": self.failure_reasons,
            "attempts": self.attempts,
        }
