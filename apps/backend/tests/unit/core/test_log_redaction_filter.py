"""Unit tests for LogRedactionFilter (PDFX-E007-F003)."""

from typing import Any

import pytest

from app.core.log_redaction_filter import LogRedactionFilter

DEFAULT_DENYLIST = ["pdf_bytes", "raw_output", "extracted_value", "prompt", "field_values"]


def _filter() -> LogRedactionFilter:
    return LogRedactionFilter(
        redacted_keys=DEFAULT_DENYLIST,
        max_value_length=500,
    )


def _call(filt: LogRedactionFilter, **fields: Any) -> dict[str, Any]:
    return filt(None, "info", dict(fields))


def test_removes_pdf_bytes_key() -> None:
    out = _call(_filter(), event="test", pdf_bytes=b"hello")
    assert "pdf_bytes" not in out


def test_removes_extracted_value_key_with_sensitive_string() -> None:
    out = _call(_filter(), event="test", extracted_value="$1,847.50")
    assert "extracted_value" not in out
    assert all("$1,847.50" not in str(v) for v in out.values())


@pytest.mark.parametrize("key", DEFAULT_DENYLIST)
def test_removes_each_denylisted_key(key: str) -> None:
    out = _call(_filter(), event="test", **{key: "any value"})
    assert key not in out


def test_truncates_long_string_under_non_denylisted_key() -> None:
    long = "x" * 1000
    out = _call(_filter(), event="test", message=long)
    assert out["message"] == "x" * 500 + "... [truncated]"


def test_does_not_truncate_499_char_string() -> None:
    s = "x" * 499
    out = _call(_filter(), event="test", message=s)
    assert out["message"] == s


def test_does_not_truncate_500_char_string() -> None:
    s = "x" * 500
    out = _call(_filter(), event="test", message=s)
    assert out["message"] == s


def test_truncates_501_char_string() -> None:
    s = "x" * 501
    out = _call(_filter(), event="test", message=s)
    assert out["message"] == "x" * 500 + "... [truncated]"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("request_id", "abc123"),
        ("skill_name", "invoice"),
        ("skill_version", "1.0.0"),
        ("output_mode", "json"),
        ("duration_ms", 1234),
        ("outcome", "success"),
        ("attempts_per_field", {"total": 4}),
        ("error_code", "VALIDATION_FAILED"),
    ],
)
def test_allowlisted_keys_pass_through_unchanged(key: str, value: object) -> None:
    out = _call(_filter(), event="test", **{key: value})
    assert key in out
    assert out[key] == value


def test_custom_max_value_length_and_denylist_overrides_are_honored() -> None:
    filt = LogRedactionFilter(redacted_keys=["secret"], max_value_length=10)
    out = filt(None, "info", {"event": "test", "secret": "x", "msg": "y" * 50})
    assert "secret" not in out
    assert out["msg"] == "y" * 10 + "... [truncated]"


def test_event_key_is_never_redacted_or_truncated() -> None:
    """The structlog 'event' key holds the log message itself; tests confirm it survives."""
    filt = _filter()
    out = filt(None, "info", {"event": "some event name"})
    assert out["event"] == "some event name"
