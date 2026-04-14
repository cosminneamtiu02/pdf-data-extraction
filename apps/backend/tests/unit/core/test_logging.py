"""Unit tests for the structlog configuration in app.core.logging."""

import structlog

from app.core.log_redaction_filter import LogRedactionFilter
from app.core.logging import configure_logging


def test_configure_logging_inserts_redaction_filter_into_chain() -> None:
    configure_logging(
        log_level="info",
        json_output=True,
        redacted_keys=["pdf_bytes", "raw_output"],
        max_value_length=500,
    )
    processors = structlog.get_config()["processors"]
    assert any(isinstance(p, LogRedactionFilter) for p in processors)


def test_redaction_filter_runs_after_merge_contextvars() -> None:
    """merge_contextvars must run before the redaction filter so bound vars survive."""
    configure_logging(
        log_level="info",
        json_output=True,
        redacted_keys=["pdf_bytes"],
        max_value_length=500,
    )
    processors = structlog.get_config()["processors"]
    merge_idx = next(
        i for i, p in enumerate(processors) if p is structlog.contextvars.merge_contextvars
    )
    redact_idx = next(i for i, p in enumerate(processors) if isinstance(p, LogRedactionFilter))
    assert merge_idx < redact_idx
