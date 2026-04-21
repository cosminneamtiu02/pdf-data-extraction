"""Unit tests for the SubBlockMatcher three-step fallback chain.

Every scenario in the feature spec PDFX-E005-F002 is covered here. The matcher
locates an extracted value inside a block's text via a three-step fallback
chain (direct substring → whitespace-normalized → NFKC-normalized) and returns
a CharRange whose indices ALWAYS refer to the original block_text.
"""

import pytest
from structlog.testing import capture_logs

from app.features.extraction.coordinates import sub_block_matcher as sub_block_matcher_module
from app.features.extraction.coordinates.char_range import CharRange
from app.features.extraction.coordinates.sub_block_matcher import SubBlockMatcher


def test_direct_hit_returns_range_in_original_block_text() -> None:
    matcher = SubBlockMatcher()

    result = matcher.locate("Total: $1,847.50 due", "$1,847.50")

    assert result == CharRange(start=7, end=16)


def test_nbsp_drift_succeeds_via_whitespace_step() -> None:
    matcher = SubBlockMatcher()
    block_text = "Total:\u00a0$1,847.50"
    value = "Total: $1,847.50"

    result = matcher.locate(block_text, value)

    assert result == CharRange(start=0, end=len(block_text))


def test_collapsed_whitespace_run_translates_back_to_original_indices() -> None:
    matcher = SubBlockMatcher()
    block_text = "Total:    $1,847.50"
    value = "Total: $1,847.50"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)
    assert block_text[result.start : result.end] == block_text


def test_leading_edge_convention_for_collapsed_runs() -> None:
    matcher = SubBlockMatcher()
    block_text = "a    b"
    value = "a b"

    result = matcher.locate(block_text, value)

    assert result == CharRange(start=0, end=6)


def test_ligature_drift_succeeds_via_nfkc_step() -> None:
    matcher = SubBlockMatcher()
    block_text = "\ufb01nal score"
    value = "final score"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)


def test_nfkc_full_width_to_half_width_matches() -> None:
    matcher = SubBlockMatcher()
    block_text = "price\uff1a\uff11\uff10\uff10"
    value = "price:100"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)


def test_total_miss_returns_none() -> None:
    matcher = SubBlockMatcher()

    result = matcher.locate("The cat sat on the mat", "$1,847.50")

    assert result is None


def test_empty_block_text_returns_none_for_non_empty_value() -> None:
    matcher = SubBlockMatcher()

    assert matcher.locate("", "foo") is None


def test_empty_value_returns_vacuous_char_range_zero_zero() -> None:
    matcher = SubBlockMatcher()

    assert matcher.locate("anything at all", "") == CharRange(0, 0)


def test_multiple_occurrences_returns_lowest_index() -> None:
    matcher = SubBlockMatcher()

    result = matcher.locate("$5 plus $5", "$5")

    assert result == CharRange(start=0, end=2)


def test_whitespace_drift_goes_through_step_two_and_succeeds() -> None:
    """A pure whitespace-collapse fixture: step 1 fails, step 2 resolves it.

    `"a    b"` vs `"a b"`: step 1 fails (no direct match because the block has
    4 spaces and the value has 1). Step 2 collapses both sides to `"a b"` and
    finds the match. Step 3 would ALSO resolve this (step 3 is a strict
    superset of step 2 since PDFX-E005-F002 hardening), but step 2 is cheaper
    and is tried first — verified by the isolated assertion that step 1 alone
    returned -1, so the matcher must have fallen through to step 2.
    """
    block_text = "a    b"
    value = "a b"

    assert block_text.find(value) == -1

    matcher = SubBlockMatcher()
    result = matcher.locate(block_text, value)
    assert result == CharRange(start=0, end=6)


def test_ligature_drift_only_matches_at_step_three_not_steps_one_or_two() -> None:
    block_text = "\ufb01nal score"
    value = "final score"
    assert block_text.find(value) == -1

    def _collapse_ws(s: str) -> str:
        return " ".join(s.split())

    assert _collapse_ws(block_text).find(_collapse_ws(value)) == -1

    matcher = SubBlockMatcher()
    result = matcher.locate(block_text, value)
    assert result is not None


def test_nfkc_does_not_paper_over_arbitrary_typos() -> None:
    matcher = SubBlockMatcher()

    result = matcher.locate("\ufb01nal score", "xinal score")

    assert result is None


def test_case_sensitivity_is_preserved() -> None:
    matcher = SubBlockMatcher()

    result = matcher.locate("Total: $5", "total: $5")

    assert result is None


def test_pure_function_no_shared_state_between_calls() -> None:
    matcher = SubBlockMatcher()

    first = matcher.locate("Total: $1,847.50 due", "$1,847.50")
    second = matcher.locate("Total: $1,847.50 due", "$1,847.50")

    assert first == second == CharRange(start=7, end=16)

    _ = matcher.locate("Total:\u00a0$1,847.50", "Total: $1,847.50")

    third = matcher.locate("Total: $1,847.50 due", "$1,847.50")
    assert third == CharRange(start=7, end=16)


def test_value_containing_regex_metacharacters_matches_as_literal() -> None:
    matcher = SubBlockMatcher()
    block_text = "price: $5.00 (net)"
    value = "$5.00 (net)"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result == CharRange(start=7, end=18)
    assert block_text[result.start : result.end] == value


def test_mixed_whitespace_kinds_collapse_through_step_two() -> None:
    matcher = SubBlockMatcher()
    block_text = "a\t\n  b"
    value = "a b"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)


def test_value_longer_than_block_text_returns_none() -> None:
    matcher = SubBlockMatcher()

    result = matcher.locate("short", "this is a much longer value")

    assert result is None


def test_mixed_drift_multi_space_plus_ligature_resolves_via_step_three() -> None:
    """Regression for PR review: multi-space run adjacent to a ligature.

    `"a    ﬁnal"` (4 spaces + U+FB01) vs `"a final"` (1 space + "fi"). Neither
    step 1 (direct), step 2 (whitespace-collapse alone leaves the ligature),
    nor NFKC-alone (leaves the multi-space run) can resolve it. Step 3's
    composed NFKC + whitespace-collapse pipeline handles both drifts at once.
    """
    import unicodedata

    block_text = "a    \ufb01nal"
    value = "a final"

    assert block_text.find(value) == -1

    def _collapse(s: str) -> str:
        return " ".join(s.split())

    assert _collapse(block_text).find(_collapse(value)) == -1
    assert (
        unicodedata.normalize("NFKC", block_text).find(
            unicodedata.normalize("NFKC", value),
        )
        == -1
    )

    matcher = SubBlockMatcher()
    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)


def test_mixed_drift_full_width_colon_plus_multi_space_resolves() -> None:
    """Regression: full-width compatibility char adjacent to a collapsed space run.

    `"price" + U+FF1A + "   100"` (full-width colon + 3 spaces) vs
    `"price: 100"` (ASCII colon + 1 space). NFKC alone maps the full-width
    colon but leaves the space run, and whitespace-collapse alone leaves the
    full-width colon. Only the composed step 3 handles both.
    """
    matcher = SubBlockMatcher()
    block_text = "price\uff1a   100"
    value = "price: 100"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)


def test_mixed_drift_ligature_adjacent_to_two_spaces_returns_full_range() -> None:
    """Minimal mixed-drift regression adapted from the reviewer's reproduction.

    `"a  ﬁ"` (1 space + 1 space + U+FB01) vs `"a fi"` (1 space + "fi"). The
    reviewer reported this class of input as a miss. With composed step 3 it
    resolves to a range covering the full original block.
    """
    matcher = SubBlockMatcher()
    block_text = "a  \ufb01"
    value = "a fi"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == len(block_text)


def test_combining_acute_matches_precomposed_e_acute_via_nfkc_step() -> None:
    """Regression for #50: multi-codepoint composition (e + combining acute vs e-acute).

    `"Cafe\\u0301 total"` contains `e` (U+0065) followed by a combining acute
    accent (U+0301). The value `"Caf\\u00e9"` is the precomposed form. When the
    NFKC step normalizes one character at a time the combining mark never merges
    with its base, so the match fails. The fix normalizes whole substrings so
    composition happens correctly.
    """
    matcher = SubBlockMatcher()
    block_text = "Cafe\u0301 total"
    value = "Caf\u00e9"

    result = matcher.locate(block_text, value)

    assert result is not None
    assert result.start == 0
    assert result.end == 5  # covers "Cafe\u0301" (5 code units in original)


def test_sub_block_matcher_logs_normalizer_degenerate_when_nfkc_collapses_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for #389: the defensive branch at `normalized_value == ""` must log
    `reason='normalizer_degenerate'` so the caller's `matcher_failed` log is not
    miscategorized.

    No known single Unicode character in today's NFKC tables collapses a
    non-empty value to an empty normalized form under either step 2's
    whitespace-collapse or step 3's composed NFKC+whitespace-collapse. The
    guard at line 71-72 of `sub_block_matcher.py` is defensive for future
    normalizer changes and edge cases. To exercise it through the public
    `locate()` API we monkeypatch the module-level normalizer functions with a
    stub that returns an empty normalized value for a non-empty input. That
    routes the real `_locate_with_normalizer` through the guard and verifies
    the log event.
    """

    def _degenerate_normalizer(_text: str) -> tuple[str, list[int]]:
        return ("", [])

    # Force BOTH step 2 and step 3 normalizers to produce empty so the direct
    # match (step 1) fails for a non-substring value and both fallback steps
    # hit the guard.
    monkeypatch.setattr(
        sub_block_matcher_module,
        "_collapse_whitespace_with_map",
        _degenerate_normalizer,
    )
    monkeypatch.setattr(
        sub_block_matcher_module,
        "_nfkc_then_collapse_whitespace_with_map",
        _degenerate_normalizer,
    )

    matcher = SubBlockMatcher()
    with capture_logs() as cap_logs:
        result = matcher.locate("some block text", "value-not-in-block")

    assert result is None
    degenerate_events = [
        entry for entry in cap_logs if entry.get("event") == "normalizer_degenerate"
    ]
    assert len(degenerate_events) >= 1
    first = degenerate_events[0]
    assert first["reason"] == "normalizer_degenerate"
    assert first["log_level"] == "info"


def test_sub_block_matcher_does_not_log_normalizer_degenerate_for_regular_miss() -> None:
    """Regression guard for #389: a genuine matcher miss (both normalizers produce
    non-empty normalized forms but no substring hit) must NOT emit the
    `normalizer_degenerate` event. Only the caller's downstream
    `matcher_failed` path should fire in that case.
    """
    matcher = SubBlockMatcher()

    with capture_logs() as cap_logs:
        result = matcher.locate("The cat sat on the mat", "$1,847.50")

    assert result is None
    degenerate_events = [
        entry for entry in cap_logs if entry.get("event") == "normalizer_degenerate"
    ]
    assert degenerate_events == []
