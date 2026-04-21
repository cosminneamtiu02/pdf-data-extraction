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
import yaml


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

# Duplicates across BOTH top-level error-code AND inside `params` in a
# single YAML. The regex only walked the top-level-under-`errors:` indent,
# so a file with duplicates at two different depths at once was only ever
# half-detected (or entirely invisible, depending on indent shape). The
# loader catches the FIRST duplicate it hits at parse time and raises —
# we assert "duplicate" in the message regardless of which pair wins.
MULTI_LEVEL_DUPLICATE_YAML = (
    "version: 1\n"
    "errors:\n"
    "  FIRST_ERROR:\n"
    "    http_status: 400\n"
    "    description: First\n"
    "    params:\n"
    "      widget_id: string\n"
    "      widget_id: integer\n"
    "  FIRST_ERROR:\n"
    "    http_status: 500\n"
    "    description: Top-level dup\n"
    "    params: {}\n"
)


@pytest.mark.parametrize(
    "yaml_text",
    [
        pytest.param(FLOW_STYLE_DUPLICATE_YAML, id="flow_style"),
        pytest.param(TRAILING_WHITESPACE_DUPLICATE_YAML, id="trailing_whitespace"),
        pytest.param(QUOTED_FORM_DUPLICATE_YAML, id="quoted_form"),
        pytest.param(NESTED_DUPLICATE_PARAMS_YAML, id="nested_params_mapping"),
        pytest.param(MULTI_LEVEL_DUPLICATE_YAML, id="multi_level_duplicate"),
    ],
)
def test_duplicate_keys_are_rejected(tmp_path: Path, yaml_text: str) -> None:
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


def test_generic_yaml_error_is_wrapped_as_value_error_with_path_prefix(
    tmp_path: Path,
) -> None:
    """Non-duplicate YAML-layer failures (ParserError/ScannerError) must also
    surface as ValueError with the source path prepended.

    `load_and_validate` catches every ``yaml.YAMLError`` (the superclass of
    ConstructorError, ParserError, ScannerError, ReaderError, …) and
    re-raises as ValueError with the path prefix so callers keep a single
    except-ValueError handler regardless of which YAML-layer failure
    occurred. Without this test, the duplicate-key tests pass but a
    regression that unwrapped a ParserError (for example) would slip
    through silently.
    """
    malformed_yaml = (
        "version: 1\n"
        "errors:\n"
        "  BAD_ERROR:\n"
        "    http_status: 400\n"
        "    description: unclosed flow\n"
        "    params: {unterminated\n"
    )
    path = tmp_path / "errors.yaml"
    path.write_text(malformed_yaml)

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError) as exc_info:
        load_and_validate(path)

    assert str(path) in str(exc_info.value)


def test_load_and_validate_does_not_call_yaml_safe_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin for issue #322: ``load_and_validate`` MUST route
    through ``yaml.load(Loader=DuplicateKeyDetectingSafeLoader)`` and
    MUST NOT call ``yaml.safe_load``.

    The behavioural duplicate-key tests above happen to pass even if the
    production code calls ``yaml.safe_load`` AND ALSO runs a
    duplicate-detecting regex pass — so if someone ever reverted the
    loader wiring and re-added the regex as a guard (exactly the
    intermediate state issue #322 documented), the behavioural tests
    above would still turn green while the load path silently lost its
    real YAML-aware duplicate check. This test pins the call itself:
    patch ``yaml.safe_load`` in the module under test so ANY invocation
    raises, then confirm ``load_and_validate`` runs cleanly on a
    well-formed file. If the fix is ever reverted to ``yaml.safe_load``
    the sentinel fires.
    """
    from scripts import generate

    def _fail_if_called(*_args: object, **_kwargs: object) -> object:
        message = (
            "load_and_validate must route through yaml.load(Loader=...) "
            "not yaml.safe_load (issue #322)."
        )
        raise AssertionError(message)

    monkeypatch.setattr(generate.yaml, "safe_load", _fail_if_called)

    path = tmp_path / "errors.yaml"
    path.write_text(BASELINE_NO_DUPLICATE_YAML)

    data = generate.load_and_validate(path)
    assert set(data["errors"].keys()) == {"FIRST_ERROR", "SECOND_ERROR"}


def test_load_and_validate_uses_duplicate_key_detecting_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin for issue #322: ``load_and_validate`` MUST pass the
    ``DuplicateKeyDetectingSafeLoader`` class (or a subclass of it) as
    the ``Loader`` argument to ``yaml.load``.

    A weaker version of this assertion — "the tests above pass" — would
    still be satisfied by any loader that happens to reject duplicates
    (e.g. a hand-rolled regex wired into a custom loader). Pinning the
    exact class closes the door on "accidentally working" replacements
    and keeps the design intent (the shared pattern with
    ``app.features.extraction.skills._duplicate_key_safe_loader``)
    enforceable as a future-proofing invariant.
    """
    from scripts import generate
    from scripts._duplicate_key_safe_loader import DuplicateKeyDetectingSafeLoader

    # Annotate as ``type[yaml.SafeLoader]``: ``DuplicateKeyDetectingSafeLoader``
    # is a ``yaml.SafeLoader`` subclass, and ``yaml.SafeLoader`` is NOT a
    # subclass of ``yaml.Loader`` (they are siblings sharing the Reader/
    # Scanner/Parser/Composer mix-in chain but a disjoint Constructor
    # hierarchy). Annotating the captured loaders as ``type[yaml.Loader]``
    # would mis-describe the runtime value and break Pyright strict.
    captured_loaders: list[type[yaml.SafeLoader]] = []
    real_yaml_load = generate.yaml.load

    def _capture_loader(
        stream: str,
        Loader: type[yaml.SafeLoader],  # noqa: N803 — mirrors PyYAML signature
    ) -> object:
        # load_and_validate reads the file with Path.read_text(), so the
        # stream arg is always a str at this call site. The narrower type
        # also keeps pyright strict happy against PyYAML's _ReadStream
        # protocol.
        captured_loaders.append(Loader)
        return real_yaml_load(stream, Loader=Loader)

    monkeypatch.setattr(generate.yaml, "load", _capture_loader)

    path = tmp_path / "errors.yaml"
    path.write_text(BASELINE_NO_DUPLICATE_YAML)

    generate.load_and_validate(path)

    assert captured_loaders, "yaml.load was never invoked"
    assert all(
        issubclass(loader, DuplicateKeyDetectingSafeLoader)
        for loader in captured_loaders
    ), (
        f"Expected every yaml.load call to receive DuplicateKeyDetectingSafeLoader "
        f"(or a subclass), got: {[loader.__name__ for loader in captured_loaders]}"
    )
