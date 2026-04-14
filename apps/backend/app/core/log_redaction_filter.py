"""Structlog processor that strips forbidden keys and truncates long strings.

The filter is the safety net for the redaction policy declared in PDFX-E007-F003:
forbidden keys (raw PDF bytes, extracted values, full prompts) are removed from
the event dict outright, and long string values under any other key are truncated
so operators see something without the full payload landing in logs. Call sites
should not include forbidden fields in the first place; this filter exists so
that an accidental log statement cannot leak document content.
"""

from collections.abc import MutableMapping
from typing import Any

# The structlog 'event' key carries the log message itself and must always
# survive both redaction and truncation, regardless of length.
_EVENT_KEY = "event"
_TRUNCATION_SUFFIX = "... [truncated]"


class LogRedactionFilter:
    """Structlog processor enforcing key-removal and value-truncation rules."""

    def __init__(self, *, redacted_keys: list[str], max_value_length: int) -> None:
        self._redacted_keys: frozenset[str] = frozenset(redacted_keys)
        self._max_value_length: int = max_value_length

    def __call__(
        self,
        _logger: Any,
        _method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]:
        result: dict[str, Any] = {}
        for key, value in event_dict.items():
            if key in self._redacted_keys:
                continue
            if key != _EVENT_KEY and isinstance(value, str) and len(value) > self._max_value_length:
                result[key] = value[: self._max_value_length] + _TRUNCATION_SUFFIX
            else:
                result[key] = value
        return result
