"""Structlog processor that strips forbidden keys and truncates long strings.

The filter is the safety net for the redaction policy declared in PDFX-E007-F003:
forbidden keys (raw PDF bytes, extracted values, full prompts) are removed from
the event dict outright, and long string values under any other key are truncated
so operators see something without the full payload landing in logs. Call sites
should not include forbidden fields in the first place; this filter exists so
that an accidental log statement cannot leak document content.
"""

from collections.abc import Mapping, MutableMapping
from typing import Any, cast

# The structlog 'event' key carries the log message itself and must always
# survive redaction. Even if 'event' is misconfigured into the denylist, the
# filter refuses to drop it — losing the message body would silently destroy
# operator visibility.
_EVENT_KEY = "event"
_TRUNCATION_SUFFIX = "... [truncated]"


class LogRedactionFilter:
    """Structlog processor enforcing key-removal and value-truncation rules.

    Walks the event dict recursively so a forbidden key nested inside a dict
    value (e.g. ``log.info("evt", context={"extracted_value": "..."})``) is
    still stripped. Long string values under non-denylisted keys are truncated
    to ``max_value_length`` characters with a ``... [truncated]`` suffix so
    operators see something without the full payload landing in logs.
    """

    def __init__(self, *, redacted_keys: list[str], max_value_length: int) -> None:
        self._redacted_keys: frozenset[str] = frozenset(redacted_keys)
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
            if not is_event and key in self._redacted_keys:
                continue
            result[key] = self._scrub_value(value, preserve_length=is_event)
        return result

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
        if not preserve_length and isinstance(value, str) and len(value) > self._max_value_length:
            return value[: self._max_value_length] + _TRUNCATION_SUFFIX
        return value
