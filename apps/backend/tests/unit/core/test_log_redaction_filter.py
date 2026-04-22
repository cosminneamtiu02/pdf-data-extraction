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


@pytest.mark.parametrize(
    "fragment",
    [
        'File "app/foo.py", line 1, in <module>',
        "python3.13",
        "timeout=0.5",
        "v1.0.2",
        "duration=1.25",
    ],
)
def test_exception_key_preserves_short_decimals_and_versions(fragment: str) -> None:
    """Short decimals (versions, floats, timeouts) in tracebacks must pass through.

    The numeric pattern targets 4+ digit sequences and thousands-separated
    amounts; version strings like ``python3.13`` and short floats like
    ``timeout=0.5`` are not PII and mangling them destroys traceback readability.
    """
    traceback = f"Traceback (most recent call last):\n  {fragment}\nValueError: boom"
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert fragment in out["exception"]


def test_exception_key_redacts_thousands_separated_amount() -> None:
    """Thousands-separated monetary amounts (e.g. ``1,847.50``) are scrubbed."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "ValueError: invoice total was 1,847.50 dollars"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert "1,847.50" not in out["exception"]
    assert "1,847" not in out["exception"]


@pytest.mark.parametrize(
    "line_number",
    ["42", "1234", "9999", "12345"],
)
def test_exception_key_preserves_frame_line_numbers(line_number: str) -> None:
    """Traceback frame line references (``line N``) must survive redaction.

    Large files legitimately produce 4+ digit line numbers; mangling them to
    ``[REDACTED_NUMBER]`` destroys the file/line locator that operators need
    to pin incident root causes. The numeric pattern excludes ``line N``
    specifically via a negative lookbehind.
    """
    traceback = (
        "Traceback (most recent call last):\n"
        f'  File "app/foo.py", line {line_number}, in handler\n'
        "ValueError: boom"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert f"line {line_number}" in out["exception"]


def test_exception_key_still_redacts_long_numbers_not_after_line_keyword() -> None:
    """Lookbehind only spares ``line N``; bare long numerics are still scrubbed."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "ValueError: account 987654321 has a balance of 4321.00"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert "987654321" not in out["exception"]
    assert "4321" not in out["exception"]


@pytest.mark.parametrize(
    "request_id",
    [
        # Mixed hex with long internal digit run; word-boundary flanking hex
        # letters currently shields this from the regex, but the invariant is
        # load-bearing for log correlation and must be pinned explicitly.
        "abc12345678def0123456789abcdef01",
        # All-digits uuid4.hex (statistically rare but valid `[a-f0-9]{32}`);
        # without explicit preservation the 32-digit run trips `\b\d{4,}\b`.
        "00000000000000000000000000000000",
        "12345678901234567890123456789012",
        # Digit run immediately preceded by a non-hex-letter boundary and
        # terminated by a hex letter mid-id.
        "deadbeef12345678abcdef0123456789",
    ],
)
def test_exception_key_preserves_embedded_request_id(request_id: str) -> None:
    """Issue #375: a 32-char hex request_id embedded in an exception message survives scrubbing.

    Although ``request_id`` is normally bound as its own log key via
    ``merge_contextvars`` and never appears inside ``exception``, any
    f-string log call (``logger.error(f"failed for {request_id}")``) can
    smuggle the id into a rendered traceback. The numeric redaction pattern
    must preserve 32-char hex tokens so log correlation survives — mangling
    an all-digit id to ``[REDACTED_NUMBER]`` destroys the link between the
    failure and the request that caused it.
    """
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        f"RuntimeError: failed for {request_id}"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert request_id in out["exception"]


def test_exception_key_preserves_request_id_but_still_redacts_surrounding_numbers() -> None:
    """Preserving 32-char hex ids must not leak adjacent numeric PII in the same message."""
    request_id = "00000000000000000000000000000000"
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        f"RuntimeError: invoice 1847.50 for request {request_id} failed"
    )
    out = _call(_filter(), event="unhandled_exception", exception=traceback)
    assert request_id in out["exception"]
    assert "1847.50" not in out["exception"]
    assert "1847" not in out["exception"]
