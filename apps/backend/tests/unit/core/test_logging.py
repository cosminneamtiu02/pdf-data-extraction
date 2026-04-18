"""Unit tests for the structlog configuration in app.core.logging."""

import logging

import structlog

from app.core.log_redaction_filter import LogRedactionFilter
from app.core.logging import configure_logging, silence_stdlib_logger


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


def test_configure_logging_suppresses_httpx_to_warning() -> None:
    """httpx emits INFO-level request logs that add noise; must be suppressed."""
    configure_logging(log_level="info", json_output=False)
    assert logging.getLogger("httpx").level == logging.WARNING


def test_configure_logging_suppresses_httpcore_to_warning() -> None:
    """httpcore is the transport layer under httpx; same suppression needed."""
    configure_logging(log_level="info", json_output=False)
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_format_exc_info_runs_before_redaction_filter() -> None:
    """Issue #134: format_exc_info must render the traceback into the event dict
    before the redaction filter runs, so the filter can scrub PII from it.
    """
    configure_logging(
        log_level="info",
        json_output=True,
        redacted_keys=["pdf_bytes"],
        max_value_length=500,
    )
    processors = structlog.get_config()["processors"]
    fmt_idx = next(i for i, p in enumerate(processors) if p is structlog.processors.format_exc_info)
    redact_idx = next(i for i, p in enumerate(processors) if isinstance(p, LogRedactionFilter))
    assert fmt_idx < redact_idx


def test_silence_stdlib_logger_sets_level_on_named_logger() -> None:
    """Helper sets the level of the named stdlib logger (issue #210).

    Uses a dedicated test-only logger name so the assertion does not interfere
    with any production logger that other tests rely on.
    """
    logger_name = "issue_210_silence_helper_fixture_alpha"
    # Reset to a known baseline before the helper runs.
    logging.getLogger(logger_name).setLevel(logging.DEBUG)

    silence_stdlib_logger(logger_name, logging.WARNING)

    assert logging.getLogger(logger_name).level == logging.WARNING


def test_silence_stdlib_logger_accepts_arbitrary_level_and_name() -> None:
    """Helper must be generic: any name + any int level, not hardcoded to docling/WARNING."""
    logger_name = "issue_210_silence_helper_fixture_beta"
    logging.getLogger(logger_name).setLevel(logging.DEBUG)

    silence_stdlib_logger(logger_name, logging.ERROR)

    assert logging.getLogger(logger_name).level == logging.ERROR


def test_silence_stdlib_logger_does_not_reduce_a_stricter_existing_level() -> None:
    """Cap semantics: never lower a logger that is already stricter than the cap.

    Earlier the helper unconditionally called ``setLevel(level)``, which
    would *reduce* an already-stricter level (e.g. ERROR -> WARNING) and
    silently increase log volume. The new implementation only raises the
    floor; it leaves stricter explicit levels untouched.
    """
    logger_name = "issue_210_silence_helper_fixture_gamma"
    # Pre-configure the logger to ERROR, which is stricter (numerically
    # higher) than the WARNING cap we are about to request.
    logging.getLogger(logger_name).setLevel(logging.ERROR)

    silence_stdlib_logger(logger_name, logging.WARNING)

    # The helper must NOT have walked ERROR back down to WARNING.
    assert logging.getLogger(logger_name).level == logging.ERROR, (
        "silence_stdlib_logger reduced an existing stricter level "
        "(ERROR -> WARNING). It must only raise the floor, never lower it."
    )


def test_configure_logging_silences_docling_via_helper() -> None:
    """Docling silencing must be driven from configure_logging, not from the parser module.

    Before issue #210, `docling_document_parser.py` called
    `logging.getLogger("docling").setLevel(WARNING)` at module import time.
    That violated the "no `logging.getLogger` outside `app/core/logging.py`"
    rule. The fix moves the call inside `configure_logging(...)` via the
    `silence_stdlib_logger` helper so the containment invariant holds.
    """
    # Reset first so we are not observing a leftover from a prior test.
    logging.getLogger("docling").setLevel(logging.DEBUG)

    configure_logging(log_level="info", json_output=False)

    assert logging.getLogger("docling").level == logging.WARNING
