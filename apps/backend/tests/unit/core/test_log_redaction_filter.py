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
