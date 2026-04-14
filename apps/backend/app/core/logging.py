"""Structured logging configuration with structlog."""

import logging
import sys

import structlog

from app.core.log_redaction_filter import LogRedactionFilter


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
    redaction_filter = LogRedactionFilter(
        redacted_keys=redacted_keys or [],
        max_value_length=max_value_length,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
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

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
