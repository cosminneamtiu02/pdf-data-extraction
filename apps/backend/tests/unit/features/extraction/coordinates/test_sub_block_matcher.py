"""Unit tests for the SubBlockMatcher three-step fallback chain.

Every scenario in the feature spec PDFX-E005-F002 is covered here. The matcher
locates an extracted value inside a block's text via a three-step fallback
chain (direct substring → whitespace-normalized → NFKC-normalized) and returns
a CharRange whose indices ALWAYS refer to the original block_text.
"""

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


def test_whitespace_drift_goes_through_step_two_not_step_one_or_three() -> None:
    """A collapsed-run fixture where NFKC does NOT help isolates step 2.

    `"a    b"` vs `"a b"`: step 1 fails (no direct match), step 3 alone would
    also fail because NFKC does not collapse runs of ordinary spaces. Only
    step 2 (whitespace collapse on both sides) can resolve this.
    """
    import unicodedata

    block_text = "a    b"
    value = "a b"

    assert block_text.find(value) == -1
    assert (
        unicodedata.normalize("NFKC", block_text).find(
            unicodedata.normalize("NFKC", value),
        )
        == -1
    )

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
