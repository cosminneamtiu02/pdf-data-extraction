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


def test_event_key_survives_even_when_misconfigured_into_denylist() -> None:
    """An accidental 'event' in the denylist must not silently drop the log message."""
    filt = LogRedactionFilter(redacted_keys=["event", "secret"], max_value_length=500)
    out = filt(None, "info", {"event": "important", "secret": "x", "skill_name": "inv"})
    assert out["event"] == "important"
    assert "secret" not in out
    assert out["skill_name"] == "inv"


def test_long_event_message_is_not_truncated() -> None:
    """The event key passes through untruncated even when the message is long."""
    long = "x" * 10_000
    out = _call(_filter(), event=long)
    assert out["event"] == long


def test_nested_dict_strips_forbidden_key_at_any_depth() -> None:
    """A forbidden key inside a dict value is stripped — closes the safety-net bypass."""
    out = _call(
        _filter(),
        event="test",
        context={"extracted_value": "$1,847.50", "skill_name": "inv"},
    )
    assert "extracted_value" not in out["context"]
    assert out["context"] == {"skill_name": "inv"}
    assert all("$1,847.50" not in repr(v) for v in out.values())


def test_deeply_nested_dict_is_walked() -> None:
    out = _call(
        _filter(),
        event="test",
        outer={"inner": {"prompt": "leaked", "ok": "kept"}},
    )
    assert out["outer"]["inner"] == {"ok": "kept"}


def test_list_values_are_walked_for_nested_dicts() -> None:
    out = _call(
        _filter(),
        event="test",
        items=[{"extracted_value": "leak", "name": "a"}, {"name": "b"}],
    )
    assert out["items"] == [{"name": "a"}, {"name": "b"}]


def test_long_string_inside_nested_dict_is_truncated() -> None:
    out = _call(_filter(), event="test", ctx={"msg": "y" * 600})
    assert out["ctx"]["msg"] == "y" * 500 + "... [truncated]"


@pytest.mark.parametrize("key", ["Raw_Output", "RAW_OUTPUT", "PDF_Bytes", "Prompt"])
def test_denylisted_key_is_redacted_case_insensitively(key: str) -> None:
    """Denylist matching must be case-insensitive — ``Raw_Output`` bypasses redaction otherwise."""
    out = _call(_filter(), event="test", **{key: "sensitive content"})
    assert key not in out
    assert all("sensitive content" not in str(v) for v in out.values())


def test_long_bytes_value_under_non_denylisted_key_is_truncated() -> None:
    """Large ``bytes`` payloads must not pass through untruncated — they get a length-summary placeholder."""
    payload = b"x" * 1000
    out = _call(_filter(), event="test", blob=payload)
    # Raw 1000-byte payload must not appear verbatim in the output.
    assert out["blob"] != payload
    # The original byte length is surfaced so operators can tell something was truncated.
    assert "1000" in str(out["blob"])


def test_short_bytes_value_passes_through_unchanged() -> None:
    """Bytes values at or below the limit must pass through unchanged."""
    payload = b"y" * 500
    out = _call(_filter(), event="test", blob=payload)
    assert out["blob"] == payload


def test_long_bytes_inside_nested_dict_is_truncated() -> None:
    """Nested bytes payloads over the limit are also summarized."""
    out = _call(_filter(), event="test", ctx={"blob": b"z" * 600})
    assert out["ctx"]["blob"] != b"z" * 600
    assert "600" in str(out["ctx"]["blob"])


def test_exception_key_email_is_redacted() -> None:
    """Issue #134: rendered exception tracebacks must have email PII scrubbed."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "ValueError: cosmin@example.com paid the fee"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert "cosmin@example.com" not in out["exception"]


def test_exception_key_long_numeric_is_redacted() -> None:
    """Issue #134: rendered exception tracebacks must have numeric PII scrubbed."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "ValueError: invoice total was 1847.50 dollars"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert "1847.50" not in out["exception"]
    assert "1847" not in out["exception"]


def test_exception_key_preserves_traceback_structure() -> None:
    """Redacted exception strings still retain the traceback header so operators see the shape."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "ValueError: user@example.com did something"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert "Traceback" in out["exception"]
    assert "ValueError" in out["exception"]


def test_exception_key_oversize_string_is_truncated() -> None:
    """An exception string exceeding max_value_length is still truncated."""
    long_tb = "Traceback\n" + "x" * 1000
    out = _call(_filter(), event="unhandled_exception", exception=long_tb)
    assert out["exception"].endswith("... [truncated]")
    assert len(out["exception"]) == 500 + len("... [truncated]")


def test_non_exception_string_values_not_pattern_scrubbed() -> None:
    """Regression guard: regex scrubbing must only apply to the 'exception' key."""
    # A normal log field that happens to contain an email must pass through
    # unchanged; redaction patterns are only applied to rendered tracebacks.
    out = _call(_filter(), event="test", message="user@example.com signed in")
    assert out["message"] == "user@example.com signed in"
