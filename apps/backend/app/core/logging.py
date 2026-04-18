"""Structured logging configuration with structlog.

This module is the ONE and ONLY place in `app/` that is permitted to call
`logging.getLogger`. CLAUDE.md forbids the pattern everywhere else because
direct stdlib `logging` calls bypass the structlog processor chain (and the
redaction filter installed by `configure_logging`). The `silence_stdlib_logger`
helper below gives feature code a structured way to request suppression of a
noisy third-party logger without reaching for `logging.getLogger` directly —
new suppressions are wired from `configure_logging` below rather than from
individual feature modules at import time (issue #210).
"""

import logging
import sys

import structlog

from app.core.log_redaction_filter import LogRedactionFilter


def silence_stdlib_logger(logger_name: str, level: int) -> None:
    """Cap a third-party stdlib logger at `level` (defense against noisy deps).

    Why this helper exists: Docling, httpx, httpcore, SQLAlchemy and similar
    third-party libraries emit INFO/DEBUG logs through the stdlib ``logging``
    module. Without suppression they would flood our service's structured
    log stream. The obvious fix is
    ``logging.getLogger(name).setLevel(level)``, but CLAUDE.md bans
    ``logging.getLogger`` outside this module — the architecture test
    ``test_only_core_logging_py_uses_logging_getlogger`` enforces that.

    Centralising the call here gives three wins at once:

    1. Every suppression is visible in one file — easy to audit.
    2. Feature modules never call ``logging.getLogger`` directly, so the
       CLAUDE.md rule holds by construction.
    3. Suppression is driven by ``configure_logging`` at application
       startup, not as a module-import side effect of an unrelated feature
       module (the pattern the parser used to have at import time before
       issue #210).

    Cap semantics: this never *reduces* the EFFECTIVE log threshold of the
    target logger. If the effective level is already numerically higher than
    ``level`` (stricter — emits less) — whether explicitly set on the target
    or inherited from an ancestor — we leave the logger alone. We only raise
    the floor when the effective level is below ``level``.

    The check runs against ``logger.getEffectiveLevel()`` rather than the
    raw ``logger.level``. Example of why this matters: if the root logger
    is at ERROR and the target is NOTSET (so its effective level is ERROR
    inherited), naïvely calling ``setLevel(WARNING)`` on the target would
    *lower* the effective threshold (ERROR → WARNING) and increase log
    volume, contradicting the cap intent. Reading the effective level
    catches both explicit and inherited state in one check.

    Args:
        logger_name: The stdlib logger name to suppress
            (e.g. ``"docling"``, ``"httpx"``, ``"httpcore"``).
        level: The numeric level to cap the logger at
            (e.g. ``logging.WARNING``, ``logging.ERROR``).
    """
    logger = logging.getLogger(logger_name)
    # Use `getEffectiveLevel()` so inheritance from ancestors counts as a
    # pre-existing stricter level — otherwise setting an explicit level on
    # a NOTSET child could silently lower the effective threshold inherited
    # from the root/ancestor (Copilot-review feedback on PR #224).
    if logger.getEffectiveLevel() < level:
        logger.setLevel(level)


def configure_logging(
    *,
    log_level: str = "info",
    json_output: bool = False,
    redacted_keys: list[str] | None = None,
    max_value_length: int = 500,
) -> None:
    """Configure structlog for the application.

    Args:
        log_level: The minimum log level (debug, info, warning, error).
        json_output: If True, output JSON. If False, output pretty console format.
        redacted_keys: Keys to strip from every event dict (defense-in-depth
            redaction policy from PDFX-E007-F003). Defaults to an empty list,
            but app.main wires the policy from Settings.log_redacted_keys.
        max_value_length: Maximum length of any string value in an event dict;
            longer values are truncated.
    """
    # Reset structlog's global configuration to a clean baseline before we
    # apply the new settings. Without this, a prior ``configure_logging``
    # call leaves ``structlog.is_configured()`` True with ``cache_logger_on_first_use``
    # still caching processor-list references; a subsequent call then fails
    # to propagate the new ``redacted_keys`` / ``log_level`` / ``json_output``
    # to any consumer that already resolved a bound logger. Production calls
    # ``configure_logging`` exactly once at app start, so this is a no-op
    # there — but tests that exercise multiple ``create_app`` calls (each
    # with their own ``Settings``) depend on the latest call's values
    # actually winning. The stdlib root logger's handlers are cleared
    # explicitly further down (``root_logger.handlers.clear()``), so no
    # additional stdlib-side reset is needed here. Issue #216.
    structlog.reset_defaults()

    redaction_filter = LogRedactionFilter(
        redacted_keys=redacted_keys or [],
        max_value_length=max_value_length,
    )

    # Order matters. The redaction filter must run after merge_contextvars
    # (so bound request_id is in the dict and survives the allowlist check)
    # but before any processor that might enrich the dict with sensitive data.
    # Today no enrichment processor adds sensitive keys, but if one is added
    # later it must be placed before the redaction filter, not after.
    #
    # ``format_exc_info`` runs before the redaction filter so that any exc_info
    # tuple attached by ``logger.exception(...)`` is rendered into the
    # ``exception`` string key BEFORE the filter walks the event dict. That way
    # the filter sees the full traceback text and can scrub PII out of the
    # rendered message — rather than passing the untouched exc_info tuple down
    # to stdlib's ``ProcessorFormatter`` where rendering would happen after the
    # redaction pass has already finished (issue #134).
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.format_exc_info,
        redaction_filter,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Silence noisy third-party loggers.
    #
    # "docling" is listed here (not at Docling-parser module-import time, where
    # it used to live) so issue #210's invariant holds: only this file calls
    # `logging.getLogger`. The parser module no longer needs to import
    # `logging` at all.
    noisy_loggers = {
        "uvicorn.access": logging.WARNING,
        "sqlalchemy.engine": logging.WARNING,
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "docling": logging.WARNING,
    }
    for logger_name, level in noisy_loggers.items():
        silence_stdlib_logger(logger_name, level)
