"""Structlog processor that strips forbidden keys and truncates long strings.

The filter is the safety net for the redaction policy declared in PDFX-E007-F003:
forbidden keys (raw PDF bytes, extracted values, full prompts) are removed from
the event dict outright, and long string values under any other key are truncated
so operators see something without the full payload landing in logs. Call sites
should not include forbidden fields in the first place; this filter exists so
that an accidental log statement cannot leak document content.

For the 'exception' key produced by ``structlog.processors.format_exc_info``,
the filter additionally regex-scrubs email addresses and monetary amounts or
other long numeric identifiers from the rendered traceback string. Exception
messages (e.g. the ``args[0]`` of a ``ValueError``) pass through the rendering
verbatim and would otherwise leak PII from the exception's string
representation (issue #134).
"""

import re
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

# The structlog 'event' key carries the log message itself and must always
# survive redaction. Even if 'event' is misconfigured into the denylist, the
# filter refuses to drop it — losing the message body would silently destroy
# operator visibility.
_EVENT_KEY = "event"
# The 'exception' key is produced by ``structlog.processors.format_exc_info``
# when a log call carries ``exc_info``. Its value is a multi-line traceback
# string that may embed the original exception message verbatim; PII inside
# that message must be scrubbed before the event reaches the renderer.
_EXCEPTION_KEY = "exception"
_TRUNCATION_SUFFIX = "... [truncated]"
_REDACTED_EMAIL = "[REDACTED_EMAIL]"
_REDACTED_NUMBER = "[REDACTED_NUMBER]"

# Standard RFC-5322-ish email pattern; intentionally conservative to avoid
# matching Python identifiers, filenames with dots, or module paths in a
# traceback's frame lines.
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Long numeric sequences (4+ digits) that may be monetary amounts, identifiers,
# or phone numbers. Matches optional thousands-separators and a decimal part so
# "$1,847.50" and "1847.50" both collapse to the same placeholder. Version
# strings like "python3.13" and short decimals like "timeout=0.5" are only 1-3
# digits and therefore untouched. Traceback frame line references of the form
# "line 42" or "line 1234" are preserved explicitly via a negative lookbehind
# so file/line locators remain readable for incident debugging — large files
# legitimately produce 4+ digit line numbers, and mangling them would destroy
# traceback usefulness without scrubbing any PII.
_NUMERIC_PATTERN = re.compile(
    r"(?<!line )\b(?:\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)\b"
)
# 32-char lowercase hex tokens — the request_id format mandated by
# PDFX-E007-F003 (see ``RequestIdMiddleware``). `merge_contextvars` normally
# surfaces request_id as its own event-dict key, but an f-string log call
# (``logger.error(f"failed for {request_id}")``) can smuggle the id into a
# rendered traceback. The numeric pattern would then scrub any digit-only
# or digit-leading span that matches `\b\d{4,}\b` — destroying log
# correlation. Masking these tokens before numeric scrubbing (and restoring
# them after) pins the correlation invariant explicitly (issue #375).
_REQUEST_ID_PATTERN = re.compile(r"\b[a-f0-9]{32}\b")
_REQUEST_ID_PLACEHOLDER_PREFIX = "\x00REQID"
_REQUEST_ID_PLACEHOLDER_SUFFIX = "\x00"


class LogRedactionFilter:
    """Structlog processor enforcing key-removal and value-truncation rules.

    Walks the event dict recursively so a forbidden key nested inside a dict
    value (e.g. ``log.info("evt", context={"extracted_value": "..."})``) is
    still stripped. Denylist matching is case-insensitive (``Raw_Output`` and
    ``RAW_OUTPUT`` are redacted just like ``raw_output``) because callers may
    construct log keys from external identifiers whose casing cannot be
    guaranteed. Long string values under non-denylisted keys are truncated to
    ``max_value_length`` characters with a ``... [truncated]`` suffix; long
    ``bytes`` values are replaced with a length-summary placeholder so
    operators see the size without the full payload landing in logs.
    """

    def __init__(self, *, redacted_keys: list[str], max_value_length: int) -> None:
        # Store the denylist in case-folded form so matching ignores casing.
        # ``str.casefold`` is preferred over ``str.lower`` for Unicode keys, but
        # in practice keys are ASCII; casefold is the safe default either way.
        self._redacted_keys: frozenset[str] = frozenset(key.casefold() for key in redacted_keys)
        self._max_value_length: int = max_value_length

    def __call__(
        self,
        _logger: Any,
        _method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]:
        return self._scrub_mapping(event_dict, top_level=True)

    def _scrub_mapping(
        self,
        mapping: Mapping[Any, Any],
        *,
        top_level: bool,
    ) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        for key, value in mapping.items():
            # 'event' is sacrosanct: it holds the log message body and is never
            # dropped, even if a misconfigured denylist contains it.
            is_event = top_level and key == _EVENT_KEY
            is_exception = top_level and key == _EXCEPTION_KEY
            if not is_event and self._is_redacted_key(key):
                continue
            if is_exception and isinstance(value, str):
                # Rendered tracebacks may embed exception messages containing
                # PII from upstream ValueErrors; scrub patterns before the
                # standard truncation pass runs (issue #134).
                result[key] = self._truncate_if_oversize(self._scrub_exception_string(value))
                continue
            result[key] = self._scrub_value(value, preserve_length=is_event)
        return result

    def _scrub_exception_string(self, value: str) -> str:
        # Mask 32-char hex request_id tokens with positional placeholders before
        # running numeric redaction, then restore them verbatim. Without this,
        # an all-digit uuid4.hex or a digit-leading id smuggled into a
        # traceback via an f-string would be mangled to [REDACTED_NUMBER],
        # breaking log correlation (issue #375).
        preserved: list[str] = []

        def _mask(match: re.Match[str]) -> str:
            token = match.group(0)
            placeholder = (
                f"{_REQUEST_ID_PLACEHOLDER_PREFIX}{len(preserved)}{_REQUEST_ID_PLACEHOLDER_SUFFIX}"
            )
            preserved.append(token)
            return placeholder

        masked = _REQUEST_ID_PATTERN.sub(_mask, value)
        scrubbed = _EMAIL_PATTERN.sub(_REDACTED_EMAIL, masked)
        scrubbed = _NUMERIC_PATTERN.sub(_REDACTED_NUMBER, scrubbed)
        for index, token in enumerate(preserved):
            placeholder = f"{_REQUEST_ID_PLACEHOLDER_PREFIX}{index}{_REQUEST_ID_PLACEHOLDER_SUFFIX}"
            scrubbed = scrubbed.replace(placeholder, token)
        return scrubbed

    def _is_redacted_key(self, key: Any) -> bool:
        # Non-string keys cannot be case-folded and are not in the denylist.
        if not isinstance(key, str):
            return False
        return key.casefold() in self._redacted_keys

    def _scrub_value(self, value: Any, *, preserve_length: bool) -> Any:
        if isinstance(value, Mapping):
            return self._scrub_mapping(cast("Mapping[Any, Any]", value), top_level=False)
        if isinstance(value, list):
            return [
                self._scrub_value(item, preserve_length=False) for item in cast("list[Any]", value)
            ]
        if isinstance(value, tuple):
            return tuple(
                self._scrub_value(item, preserve_length=False)
                for item in cast("tuple[Any, ...]", value)
            )
        if preserve_length:
            return value
        return self._truncate_if_oversize(value)

    def _truncate_if_oversize(self, value: Any) -> Any:
        if isinstance(value, str) and len(value) > self._max_value_length:
            return value[: self._max_value_length] + _TRUNCATION_SUFFIX
        if isinstance(value, bytes) and len(value) > self._max_value_length:
            # Replace oversize bytes payloads with a length-summary placeholder
            # rather than a truncated prefix: binary data is rarely human-
            # readable and a byte-count tells operators what they need without
            # risking a partial leak of document content.
            return f"<bytes len={len(value)} truncated>"
        return value
