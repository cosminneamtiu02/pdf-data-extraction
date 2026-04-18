"""Unit tests for the structlog configuration in app.core.logging."""

import io
import json
import logging

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


def test_configure_logging_is_idempotent_across_two_calls_with_different_settings() -> None:
    """Issue #216: calling configure_logging twice in the same process (as
    happens when two tests each call create_app with different Settings) must
    leave a freshly-obtained logger honouring the second call's
    ``redacted_keys`` / ``log_level`` / ``json_output``, not the first.

    ``structlog.reset_defaults()`` at the top of configure_logging makes the
    second call equivalent to a clean-process first call: ``_CONFIG`` is
    rebuilt from scratch (``is_configured`` flipped False, processor-list
    reference reset to a fresh builtin copy, ``cache_logger_on_first_use``
    reset to the builtin default before being turned back on by the inline
    ``structlog.configure(...)``). This guarantees a single source of truth
    — the latest call's config — for every fresh ``structlog.get_logger``
    fetched after the call returns.

    Caveat: a proxy whose ``.info()`` was already invoked during the first
    call keeps a cached bound logger that captured the first call's
    processor-list reference by closure. ``reset_defaults()`` does not walk
    and un-cache those proxies (structlog has no API for it). The contract
    this test enforces is the fresh-fetch contract only — consumers who
    reuse proxy instances across configure calls own that lifecycle.
    """
    # First configure: denylist "first_only_key", console output.
    configure_logging(
        log_level="info",
        json_output=False,
        redacted_keys=["first_only_key"],
        max_value_length=500,
    )
    first_filter = next(
        p for p in structlog.get_config()["processors"] if isinstance(p, LogRedactionFilter)
    )
    assert "first_only_key" in first_filter._redacted_keys  # noqa: SLF001 — filter internals are the contract under test
    assert structlog.is_configured() is True

    # Exercise the first config end-to-end so cached-proxy state is realistic
    # (mimics a running test that emitted logs before a subsequent
    # create_app call reconfigures with different Settings).
    structlog.get_logger("tests.idempotency.warmup").info(
        "warmup_event",
        first_only_key="A",
        second_only_key="B",
    )

    # Second configure: flip to JSON with a different denylist and log_level.
    configure_logging(
        log_level="warning",
        json_output=True,
        redacted_keys=["second_only_key"],
        max_value_length=500,
    )

    # Contract 1: ``_CONFIG.default_processors`` reflects the second call's
    # filter on a fresh list instance (not aliased with the first call's).
    second_filter = next(
        p for p in structlog.get_config()["processors"] if isinstance(p, LogRedactionFilter)
    )
    assert second_filter is not first_filter
    assert "second_only_key" in second_filter._redacted_keys  # noqa: SLF001 — filter internals are the contract under test
    assert "first_only_key" not in second_filter._redacted_keys  # noqa: SLF001
    assert structlog.is_configured() is True

    # Contract 2: root log_level mirrors the second call.
    assert logging.getLogger().level == logging.WARNING

    # Contract 3: a freshly-obtained logger, emitting through the production
    # handler, honours the second call's JSON renderer and denylist.
    buf = io.StringIO()
    root = logging.getLogger()
    # Exactly one handler — configure_logging clears previous handlers and
    # installs a single StreamHandler. Indexing with [0] only makes sense
    # when we know there is nothing else.
    assert len(root.handlers) == 1, (
        f"configure_logging must leave exactly one handler on root, found {len(root.handlers)}"
    )
    prod_handler = root.handlers[0]
    assert isinstance(prod_handler, logging.StreamHandler)
    original_stream = prod_handler.stream
    prod_handler.setStream(buf)
    try:
        fresh_logger = structlog.get_logger("tests.idempotency.fresh")
        fresh_logger.warning(
            "fresh_event",
            first_only_key="VISIBLE_AFTER_RECONFIG",
            second_only_key="HIDDEN_AFTER_RECONFIG",
        )
    finally:
        prod_handler.setStream(original_stream)
    captured = buf.getvalue().strip()
    # Parse as JSON rather than string-matching so this test survives
    # whitespace / key-ordering changes in the structlog renderer and also
    # guarantees the output is valid JSON after json_output=True.
    event = json.loads(captured)
    assert event["event"] == "fresh_event"
    # first_only_key is no longer in the denylist → surfaces verbatim.
    assert event.get("first_only_key") == "VISIBLE_AFTER_RECONFIG"
    # second_only_key is now in the denylist → stripped by LogRedactionFilter.
    assert "second_only_key" not in event
