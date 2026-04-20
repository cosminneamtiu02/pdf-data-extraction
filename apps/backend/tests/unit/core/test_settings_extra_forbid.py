"""Regression: ``Settings`` explicitly sets ``extra="forbid"`` (issue #271).

What ``extra="forbid"`` actually does — and does NOT do — for env-sourced
configuration in pydantic-settings 2.13.1, established experimentally and
made consistent by ``FilteredDotEnvSettingsSource``:

* Constructor kwargs: ``Settings(definitely_not_a_real_field=...)`` raises
  ``ValidationError`` with ``extra_forbidden``. This is the load-bearing
  guard for issue #271 — it catches typos in programmatic construction
  sites inside the codebase.
* ``.env`` file keys: pydantic-settings' default ``DotEnvSettingsSource``
  reads every key from the dotenv file, which collides with
  ``extra="forbid"`` because we share ``.env`` with
  :class:`scripts.BenchmarkSettings` (it owns ``BENCH_*`` keys via
  ``env_prefix``). ``FilteredDotEnvSettingsSource`` (wired in
  ``app/core/filtered_dotenv_source.py``) strips keys that are not
  declared fields on :class:`Settings` before they reach validation, so
  typoed ``.env`` keys and ``BENCH_*`` siblings are silently ignored.
* Shell environment variables: pydantic-settings looks up env vars by
  name — it only reads env vars whose names match declared fields (and
  an ``env_prefix`` if configured; this project does not use one for
  :class:`Settings`). A typoed shell env var like ``OLAMA_MODEL=...``
  (missing one ``L``) is therefore never read. ``extra`` has nothing
  to forbid; this is pydantic-settings' design, not a bug.

The real defence against typoed env-var names (shell and ``.env`` alike)
is the parity check in ``tests/unit/core/test_env_example_parity.py``,
which fails on any ``.env.example`` key that does not map to a declared
field on a managed ``BaseSettings`` subclass. Operator-local ``.env``
files are outside that lint, and their typos are silently ignored —
tracked separately if the project adopts a deploy-time lint.

Why lock ``extra="forbid"`` in the source of truth: ``BaseSettings``
currently defaults to ``forbid`` in 2.13.x, but the default is an
implementation detail and could change in a future major. Declaring it
explicitly in ``SettingsConfigDict`` makes the contract part of this
file. The AST assertion below walks the class body so harmless
refactors (quote-style change, whitespace, splitting ``model_config``
across lines) don't trip the check.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _model_config_extra_from_ast() -> str | None:
    """Return the ``extra=`` argument value from ``Settings.model_config`` via AST.

    Parses ``Settings`` source, walks its class body for an
    ``model_config = SettingsConfigDict(...)`` assignment, and returns
    the string literal passed as ``extra=``. Tolerates both single and
    double quotes (the AST stores the string value, not quote style).
    Returns ``None`` if no such assignment or argument is found, so
    callers can distinguish "declared explicitly" from "inherited".
    """
    source = textwrap.dedent(inspect.getsource(Settings))
    tree = ast.parse(source)
    class_def = tree.body[0]
    assert isinstance(class_def, ast.ClassDef), "expected top-level node to be Settings class"
    for node in class_def.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "model_config" for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        for keyword in node.value.keywords:
            if keyword.arg != "extra":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                return keyword.value.value
    return None


def test_settings_declares_extra_forbid_in_class_body() -> None:
    """The ``model_config = SettingsConfigDict(extra="forbid", ...)`` assignment
    must live in ``Settings``' own class body, not be inherited from
    pydantic-settings' default.

    Asserting on ``Settings.model_config.get("extra") == "forbid"`` alone
    would pass even if a developer deleted the explicit declaration —
    the inherited pydantic-settings default is already ``"forbid"`` in
    2.13.x. Walking the AST of ``Settings``' own source catches removal
    of the explicit line, which is the load-bearing part of issue #271's
    fix: the contract lives in this repo, not in whatever default the
    upstream library happens to ship.
    """
    extra = _model_config_extra_from_ast()
    assert extra == "forbid", (
        'Settings.model_config must declare extra="forbid" explicitly in '
        "SettingsConfigDict so the contract survives upstream upgrades that "
        f"might change the inherited default. AST saw extra={extra!r}."
    )


def test_settings_model_config_runtime_extra_is_forbid() -> None:
    """The runtime ``model_config["extra"]`` value must be ``"forbid"``.

    Independent of the AST assertion above, verify the value that
    pydantic-settings actually uses at construction time. A future
    refactor could, for instance, move ``model_config`` to a shared
    base class — the AST assertion would then fail, but this runtime
    assertion would keep passing, which is the desired behavior.
    Pairing both locks the invariant from both angles.
    """
    assert Settings.model_config.get("extra") == "forbid", (
        "Settings.model_config['extra'] must resolve to 'forbid' at runtime."
    )


def test_settings_rejects_unknown_kwargs() -> None:
    """Unknown field names raise ``ValidationError`` with ``extra_forbidden``.

    ``_env_file=None`` prevents any project-root ``.env`` from influencing
    the error shape (otherwise a developer-local ``.env`` could add
    unrelated errors to the exception). Asserts on ``type ==
    "extra_forbidden"`` (the pydantic error code documented at
    https://errors.pydantic.dev/2.13/v/extra_forbidden) rather than the
    human-readable message, so wording tweaks in future pydantic
    releases do not break the test.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, definitely_not_a_real_field="x")  # type: ignore[call-arg]

    error_types = [err["type"] for err in exc_info.value.errors()]
    assert "extra_forbidden" in error_types, (
        f"Expected 'extra_forbidden' in {error_types}; "
        "Settings must reject unknown fields instead of silently ignoring them."
    )


def test_extra_forbid_does_not_catch_typoed_env_var_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typoed shell env var names are silently ignored — documented, not fixed.

    pydantic-settings resolves env vars by name-lookup: it reads only the
    env var names that match a declared field (with ``env_prefix``
    applied; this project does not set one on :class:`Settings`). A
    misspelled shell env var like ``OLAMA_MODEL`` (one ``L``) is
    therefore never read — ``extra="forbid"`` has nothing to forbid
    because the misspelled key never enters the Settings construction
    inputs.

    This test pins that behavior so future readers don't mistakenly
    believe ``extra="forbid"`` catches shell env-name typos. The real
    defence against shell env-name typos would be an explicit
    allowlist check against ``Settings.model_fields`` combined with a
    deploy-time lint — tracked separately if the project decides to
    adopt it.
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


def test_unrelated_dotenv_keys_are_silently_ignored(tmp_path: Path) -> None:
    """Shared ``.env`` keys owned by other settings classes must be tolerated.

    ``apps/backend/.env.example`` is copied verbatim to ``.env`` by
    ``docs/new-project-setup.md``'s ``cp .env.example .env`` step. The
    example file ships ``BENCH_*`` keys for
    :class:`scripts.BenchmarkSettings` alongside :class:`Settings` keys
    (issues #237, #272). Without filtering, ``Settings(_env_file=".env")``
    would raise seven ``extra_forbidden`` errors at startup on every
    fresh clone, regressing onboarding.

    :class:`app.core.filtered_dotenv_source.FilteredDotEnvSettingsSource`
    strips dotenv keys that are not declared fields on :class:`Settings`
    before they reach validation. This test encodes the observed
    behavior: unrelated ``.env`` keys must not cause ``extra_forbidden``,
    and declared-field overrides from the same file must still win.
    """
    env_path = tmp_path / ".env"
    env_path.write_text(
        textwrap.dedent(
            """
            # Declared Settings field override.
            OLLAMA_MODEL=dotenv-override-model
            # Keys owned by BenchmarkSettings — must be silently ignored,
            # not raise ``extra_forbidden`` against :class:`Settings`.
            BENCH_WARMUP=5
            BENCH_URL=http://localhost:8000
            # Typo in a would-be Settings key — silently ignored, same
            # fate as a typoed shell env var. The parity check in
            # ``test_env_example_parity.py`` catches this class of typo
            # at commit time for ``.env.example``; operator-local
            # ``.env`` is outside that lint.
            OLLMA_MODEL=silently-ignored-typo
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=str(env_path))  # type: ignore[call-arg]

    # Declared-field override from ``.env`` still wins.
    assert settings.ollama_model == "dotenv-override-model"
    # Construction succeeded — no ``extra_forbidden`` raised for
    # ``BENCH_*`` siblings or typoed keys. That is the guarantee this
    # test locks in for the shared-``.env`` setup flow.
