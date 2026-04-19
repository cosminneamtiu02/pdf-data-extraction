"""Tests for duplicate-key detection in errors.yaml.

The previous regex-based `_detect_duplicate_keys` only matched lines of the
shape ``^  (\\w+):$``, so it silently let through any duplicate that used
leading whitespace other than exactly two spaces, trailing whitespace
before the colon, a quoted-form key (``"KEY":``), flow-style mappings
(all on one line), or mappings nested deeper than the two-space-indent
level the regex baked in.

These edge cases are all valid YAML — PyYAML's ``SafeLoader`` happily
parses them and last-wins-collapses the duplicates. The replacement must
catch every duplicate-key situation via a real YAML-aware parse, not only
the narrow surface the regex happened to recognise (issue #294).
"""

from pathlib import Path

import pytest


BASELINE_NO_DUPLICATE_YAML = """
version: 1
errors:
  FIRST_ERROR:
    http_status: 404
    description: First
    params: {}
  SECOND_ERROR:
    http_status: 500
    description: Second
    params: {}
"""

# Flow-style mapping on one line. The regex scans line-by-line and expects
# every candidate key to sit on its own line indented by exactly two
# spaces — so it cannot possibly see the duplicate here even though
# PyYAML silently collapses it to the last entry.
FLOW_STYLE_DUPLICATE_YAML = (
    "version: 1\n"
    "errors: {MY_KEY: {http_status: 404, params: {}}, "
    "MY_KEY: {http_status: 500, params: {}}}\n"
)

# Trailing whitespace between key and colon (``MY_KEY :``). The regex's
# ``:$`` anchor requires the colon at line-end with no space between the
# key token and the colon, so this silently slips past.
TRAILING_WHITESPACE_DUPLICATE_YAML = (
    "version: 1\n"
    "errors:\n"
    "  MY_KEY :\n"
    "    http_status: 404\n"
    "    description: First\n"
    "    params: {}\n"
    "  MY_KEY :\n"
    "    http_status: 500\n"
    "    description: Dup\n"
    "    params: {}\n"
)

# Quoted-form key followed by the same key in bare form. Both refer to
# the same mapping key per the YAML spec and PyYAML collapses them
# last-wins; the regex's ``\\w+`` term never matches the leading quote so
# the first occurrence is invisible to the seen-set.
QUOTED_FORM_DUPLICATE_YAML = (
    "version: 1\n"
    "errors:\n"
    '  "MY_KEY":\n'
    "    http_status: 404\n"
    "    description: First\n"
    "    params: {}\n"
    "  MY_KEY:\n"
    "    http_status: 500\n"
    "    description: Dup\n"
    "    params: {}\n"
)

# Duplicate INSIDE a nested ``params`` mapping. The regex only tracks
# keys at the top-level-under-``errors:`` indent; any duplicate nested
# deeper is entirely out of scope for the old implementation.
NESTED_DUPLICATE_PARAMS_YAML = (
    "version: 1\n"
    "errors:\n"
    "  MY_KEY:\n"
    "    http_status: 422\n"
    "    description: Nested dup\n"
    "    params:\n"
    "      widget_id: string\n"
    "      widget_id: integer\n"
)


@pytest.mark.parametrize(
    ("name", "yaml_text"),
    [
        ("flow_style", FLOW_STYLE_DUPLICATE_YAML),
        ("trailing_whitespace", TRAILING_WHITESPACE_DUPLICATE_YAML),
        ("quoted_form", QUOTED_FORM_DUPLICATE_YAML),
        ("nested_params_mapping", NESTED_DUPLICATE_PARAMS_YAML),
    ],
)
def test_duplicate_keys_are_rejected(tmp_path: Path, name: str, yaml_text: str) -> None:
    """Every duplicate-key edge case the old regex missed must now raise."""
    path = tmp_path / "errors.yaml"
    path.write_text(yaml_text)

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="[Dd]uplicate"):
        load_and_validate(path)


def test_baseline_non_duplicate_yaml_passes(tmp_path: Path) -> None:
    """A well-formed YAML without any duplicate keys must not raise.

    Guards against a regression where the replacement is over-eager and
    rejects valid input.
    """
    path = tmp_path / "errors.yaml"
    path.write_text(BASELINE_NO_DUPLICATE_YAML)

    from scripts.generate import load_and_validate

    data = load_and_validate(path)
    assert set(data["errors"].keys()) == {"FIRST_ERROR", "SECOND_ERROR"}
