"""Regression: ``Settings`` explicitly sets ``extra="forbid"`` (issue #271).

What ``extra="forbid"`` actually does — and does NOT do — for env-sourced
configuration in pydantic-settings 2.13.1, established experimentally:

* Constructor kwargs: ``Settings(definitely_not_a_real_field=...)`` raises
  ``ValidationError`` with ``extra_forbidden``. This catches typos in
  programmatic construction sites inside the codebase.
* ``.env`` file keys: pydantic-settings reads every key from the dotenv file
  (not just those matching declared fields), so a typo like ``OLLMA_MODEL=...``
  in ``.env`` surfaces as an extra input and raises ``extra_forbidden``. This
  catches typos in files checked into the repo or authored by operators.
* **Shell environment variables**: pydantic-settings looks up env vars by
  name — it only reads env vars whose names match declared fields (and an
  ``env_prefix`` if configured; this project does not use one). A typoed
  shell env var like ``OLAMA_MODEL=...`` (missing one ``L``) is NEVER READ,
  so ``extra="forbid"`` has nothing to forbid. This is a deliberate design
  choice in pydantic-settings, not a bug. The test at the bottom of this
  file documents this limitation explicitly.

Why lock ``extra="forbid"`` in the source of truth: ``BaseSettings`` currently
defaults to ``forbid`` in 2.13.x, but the default is an implementation detail
and could change in a future major. Declaring it explicitly in
``SettingsConfigDict`` makes the contract part of this file. The structural
assertion below uses ``inspect.getsource`` to detect removal of the literal
``extra="forbid"`` line, because a simple ``model_config.get("extra")`` check
would still pass on the inherited default.
"""

from __future__ import annotations

import inspect
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_config_sets_extra_forbid_explicitly() -> None:
    """The literal ``extra="forbid"`` line must appear in ``Settings`` source.

    Asserting on ``Settings.model_config.get("extra") == "forbid"`` would pass
    even if a developer deleted the explicit declaration — the inherited
    pydantic-settings default is already ``forbid`` in 2.13.x. Inspecting the
    class source catches removal of the explicit line, which is the load-
    bearing part of issue #271's fix: the contract lives in this repo, not
    in whatever default the upstream library happens to ship.
    """
    source = inspect.getsource(Settings)
    assert 'extra="forbid"' in source, (
        'Settings must declare extra="forbid" explicitly in '
        "SettingsConfigDict. Relying on the pydantic-settings inherited "
        "default is fragile across upstream upgrades."
    )


def test_settings_rejects_unknown_kwargs() -> None:
    """Unknown field names raise ``ValidationError`` with ``extra_forbidden``.

    Asserts on ``type == "extra_forbidden"`` (the pydantic error code
    documented at https://errors.pydantic.dev/2.13/v/extra_forbidden) rather
    than the human-readable message, so wording tweaks in future pydantic
    releases do not break the test.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(definitely_not_a_real_field="x")  # type: ignore[call-arg]

    error_types = [err["type"] for err in exc_info.value.errors()]
    assert "extra_forbidden" in error_types, (
        f"Expected 'extra_forbidden' in {error_types}; "
        "Settings must reject unknown fields instead of silently ignoring them."
    )


def test_extra_forbid_does_not_catch_typoed_env_var_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typoed shell env var names are silently ignored — documented, not fixed.

    pydantic-settings resolves env vars by name-lookup: it reads only the env
    var names that match a declared field (with ``env_prefix`` applied; this
    project does not set one). A misspelled shell env var like
    ``OLAMA_MODEL`` (one ``L``) is therefore never read —
    ``extra="forbid"`` has nothing to forbid because the misspelled key
    never enters the Settings construction inputs.

    This test pins that behavior so future readers don't mistakenly believe
    ``extra="forbid"`` catches shell env-name typos. The real defence
    against shell env-name typos would be an explicit allowlist check
    against ``Settings.model_fields`` combined with a deploy-time lint —
    tracked separately if the project decides to adopt it.

    The ``.env`` case is different: pydantic-settings reads every key from
    the dotenv file and applies ``extra`` to the full set, so a typo inside
    ``.env`` does raise ``extra_forbidden`` (see
    ``test_extra_forbid_catches_typoed_dotenv_keys``). This test covers
    only the shell env var limitation, which is the one that most often
    surprises operators.
    """
    # Valid env var (matches the ``ollama_model`` field, case-insensitive)
    # paired with a typoed sibling that pydantic-settings will never look up.
    monkeypatch.setenv("OLLAMA_MODEL", "custom-model")
    monkeypatch.setenv("OLAMA_MODEL", "silently-ignored-typo")

    # ``_env_file=None`` prevents any project-root ``.env`` from influencing
    # the assertion. Construction must succeed despite the typoed sibling.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Valid env var was read.
    assert settings.ollama_model == "custom-model"
    # Typoed env var was silently ignored — no ValidationError raised. This
    # is the limitation we document: ``extra="forbid"`` does NOT catch
    # typoed shell env var names.


def test_extra_forbid_catches_typoed_dotenv_keys(tmp_path: Path) -> None:
    """Typoed keys in a ``.env`` file DO raise ``extra_forbidden``.

    This is the subset of "typo" cases that ``extra="forbid"`` actually
    catches: pydantic-settings reads every key from the dotenv file (not
    just names matching declared fields), so an unknown key such as
    ``OLLMA_MODEL=...`` surfaces as an extra input and fails construction
    at startup — the behavior ``config.py``'s comment now claims.

    This test writes a temporary env file and asserts the raised error code,
    exercising the path that ``_env_file=<path>`` opens at Settings
    construction time.
    """
    env_path = tmp_path / ".env"
    env_path.write_text(
        textwrap.dedent(
            """
            OLLAMA_MODEL=custom-model
            OLLMA_MODEL=silently-typoed
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=str(env_path))  # type: ignore[call-arg]

    error_types = [err["type"] for err in exc_info.value.errors()]
    assert "extra_forbidden" in error_types, (
        f"Expected 'extra_forbidden' in {error_types}; ``extra=\"forbid\"`` "
        "must reject unknown keys loaded from ``.env`` files."
    )
