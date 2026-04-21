"""Tests for the application configuration."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_defaults() -> None:
    """Settings should construct with sensible defaults from environment or code."""
    s = Settings()
    assert s.app_env == "development"
    # log_level is normalized to uppercase by the validator (issue #146).
    assert s.log_level == "INFO"
    assert s.cors_origins == ["http://localhost:5173"]


def test_settings_accepts_overrides() -> None:
    """Settings should accept explicit overrides."""
    s = Settings(
        app_env="production",
        log_level="warning",
        cors_origins=["https://example.com"],
    )
    assert s.app_env == "production"
    # Validator normalizes lowercase inputs to canonical uppercase (issue #146).
    assert s.log_level == "WARNING"
    assert s.cors_origins == ["https://example.com"]


def test_settings_redaction_defaults() -> None:
    """log_redacted_keys and log_max_value_length default to the documented values."""
    s = Settings()
    assert s.log_max_value_length == 500
    assert s.log_redacted_keys == [
        "pdf_bytes",
        "raw_output",
        "extracted_value",
        "prompt",
        "field_values",
    ]


def test_settings_redaction_overrides() -> None:
    """Both redaction fields accept explicit overrides."""
    s = Settings(log_max_value_length=42, log_redacted_keys=["secret", "token"])
    assert s.log_max_value_length == 42
    assert s.log_redacted_keys == ["secret", "token"]


def test_settings_ollama_probe_ttl_default() -> None:
    """ollama_probe_ttl_seconds defaults to 10.0."""
    s = Settings()
    assert s.ollama_probe_ttl_seconds == 10.0


def test_settings_ollama_probe_timeout_default() -> None:
    """ollama_probe_timeout_seconds defaults to 5.0."""
    s = Settings()
    assert s.ollama_probe_timeout_seconds == 5.0


# -- ollama_base_url validation (issue #64) --------------------------------


def test_settings_ollama_base_url_empty_string_rejected() -> None:
    """An empty string for ollama_base_url must be rejected."""
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(ollama_base_url="")


def test_settings_ollama_base_url_whitespace_only_rejected() -> None:
    """A whitespace-only string for ollama_base_url must be rejected."""
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(ollama_base_url="   ")


def test_settings_ollama_base_url_no_http_scheme_rejected() -> None:
    """A URL without http:// or https:// scheme must be rejected."""
    with pytest.raises(ValidationError, match="must start with"):
        Settings(ollama_base_url="ftp://localhost:11434")


def test_settings_ollama_base_url_bare_hostname_rejected() -> None:
    """A bare hostname without scheme must be rejected."""
    with pytest.raises(ValidationError, match="must start with"):
        Settings(ollama_base_url="localhost:11434")


def test_settings_ollama_base_url_scheme_only_no_host_rejected() -> None:
    """http:// with no host must be rejected."""
    with pytest.raises(ValidationError, match="must include a host"):
        Settings(ollama_base_url="http://")


def test_settings_ollama_base_url_trailing_api_path_rejected() -> None:
    """A URL ending with /api must be rejected to prevent double /api segments."""
    with pytest.raises(ValidationError, match="must not include a trailing /api"):
        Settings(ollama_base_url="http://localhost:11434/api")


def test_settings_ollama_base_url_valid_http_accepted() -> None:
    """A valid http:// URL must be accepted."""
    s = Settings(ollama_base_url="http://localhost:11434")
    assert s.ollama_base_url == "http://localhost:11434"


def test_settings_ollama_base_url_valid_https_accepted() -> None:
    """A valid https:// URL must be accepted."""
    s = Settings(ollama_base_url="https://ollama.example.com")
    assert s.ollama_base_url == "https://ollama.example.com"


def test_settings_ollama_base_url_trailing_slash_stripped() -> None:
    """Trailing slashes must be stripped from ollama_base_url."""
    s = Settings(ollama_base_url="http://localhost:11434/")
    assert s.ollama_base_url == "http://localhost:11434"


def test_settings_ollama_base_url_whitespace_stripped() -> None:
    """Leading/trailing whitespace must be stripped before validation."""
    s = Settings(ollama_base_url="  http://localhost:11434  ")
    assert s.ollama_base_url == "http://localhost:11434"


def test_settings_ollama_base_url_credentials_not_in_repr() -> None:
    """Proxy credentials in ``ollama_base_url`` must not appear in ``repr(settings)``.

    Operators running Ollama behind an authenticated proxy embed the password
    in the URL (``http://user:pass@proxy:11434``). Any ``repr(settings)`` that
    surfaces in an exception traceback, a debug dump, or a structlog payload
    would then leak the credential. ``Field(repr=False)`` on the field tells
    Pydantic to omit the field entirely from the model's ``repr`` — both the
    name and the value — see issue #284.

    The sentinel-value assertion is robust against env-bleed: the test owns
    the value it greps for, so other settings populated from ``.env`` or
    process env cannot accidentally satisfy or break it. ``_env_file=None``
    keeps the rest of the repr deterministic.
    """
    sentinel = "PUD7HRfAlzrhN-Sentinel-284"
    credentialed_url = f"http://operator:{sentinel}@proxy.internal:11434"
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        ollama_base_url=credentialed_url,
    )
    rendered = repr(s)
    # Primary contract: the credential value must never leak.
    assert sentinel not in rendered
    assert credentialed_url not in rendered
    # ``Field(repr=False)`` omits the field entirely, so the field name
    # must also be absent. If a future pydantic release weakens that
    # contract (e.g. renders ``ollama_base_url=<hidden>``), this assertion
    # fails loudly — the credential value check above remains the
    # load-bearing security guarantee.
    assert "ollama_base_url" not in rendered


# -- cors_origins empty-string coercion (issue #58) --------------------------


def test_settings_cors_origins_empty_string_coerced_to_empty_list() -> None:
    """A programmatic empty string for cors_origins must become an empty list.

    This covers direct ``Settings(cors_origins="")`` input.  The production
    env-var path is handled by the compose default ``${CORS_ORIGINS:-[]}``;
    the validator catches the programmatic case where callers may still
    provide an empty string.
    """
    s = Settings(cors_origins="")  # type: ignore[arg-type]
    assert s.cors_origins == []


def test_settings_cors_origins_json_empty_array_accepted() -> None:
    """A JSON '[]' string for cors_origins must parse to an empty list."""
    s = Settings(cors_origins="[]")  # type: ignore[arg-type]
    assert s.cors_origins == []


def test_settings_cors_origins_json_array_parsed() -> None:
    """A JSON array string for cors_origins must parse correctly."""
    s = Settings(cors_origins='["https://a.com","https://b.com"]')  # type: ignore[arg-type]
    assert s.cors_origins == ["https://a.com", "https://b.com"]


def test_settings_cors_origins_native_list_passthrough() -> None:
    """A native Python list for cors_origins must pass through unchanged."""
    s = Settings(cors_origins=["https://example.com"])
    assert s.cors_origins == ["https://example.com"]


def test_settings_cors_origins_malformed_json_raises_clear_error() -> None:
    """A string starting with '[' but not valid JSON must produce a clear error."""
    with pytest.raises(ValidationError, match="cors_origins must be a JSON array"):
        Settings(cors_origins="[not valid json")  # type: ignore[arg-type]


# -- cors_allow_credentials (issue #346) ------------------------------------


def test_settings_cors_allow_credentials_default_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cors_allow_credentials`` must default to ``False`` (safe posture).

    The service runs without cookies or credentialed session state by default,
    so ``allow_credentials=True`` is extra attack surface we should only opt
    into explicitly. Issue #346.

    ``_env_file=None`` disables ``apps/backend/.env`` loading and
    ``monkeypatch.delenv`` clears any workstation-local override, so this
    test validates the code default rather than the developer's environment.
    """
    monkeypatch.delenv("CORS_ALLOW_CREDENTIALS", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_allow_credentials is False


def test_settings_cors_allow_credentials_accepts_true_override() -> None:
    """Operators can opt in to credentialed CORS by setting the field to True."""
    s = Settings(cors_allow_credentials=True, cors_origins=["https://example.com"])
    assert s.cors_allow_credentials is True


def test_settings_cors_allow_credentials_wildcard_origin_rejected() -> None:
    """``allow_credentials=True`` combined with wildcard origin must fail validation.

    The CORS spec forbids returning ``Access-Control-Allow-Credentials: true``
    alongside ``Access-Control-Allow-Origin: *``; Starlette silently drops
    credentials in that configuration, but the combination is a well-known
    CVE pattern worth rejecting at config-load time with a clear error.
    Issue #346.
    """
    with pytest.raises(ValidationError, match="cors_allow_credentials"):
        Settings(cors_allow_credentials=True, cors_origins=["*"])


def test_settings_cors_allow_credentials_wildcard_origin_allowed_when_false() -> None:
    """Wildcard origin is allowed when credentials are off (the default posture)."""
    s = Settings(cors_allow_credentials=False, cors_origins=["*"])
    assert s.cors_allow_credentials is False
    assert s.cors_origins == ["*"]


def test_settings_cors_origins_empty_array_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production path: compose defaults CORS_ORIGINS to '[]' when unset.

    pydantic-settings JSON-parses env vars for complex types *before*
    field validators run, so an empty string cannot be intercepted by
    a @field_validator.  The compose default ``${CORS_ORIGINS:-[]}``
    ensures the env var is always a valid JSON array.
    """
    monkeypatch.setenv("CORS_ORIGINS", "[]")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == []


# -- log_level validation (issue #146) --------------------------------------


def test_settings_log_level_typo_rejected() -> None:
    """A typo like ``debg`` must fail validation with a clear error.

    Without this validator, the typo slipped past Settings construction
    and only surfaced when structlog / the stdlib logger tried to parse
    it — crashing the app *before* the FastAPI exception handlers install.
    """
    with pytest.raises(ValidationError, match="log_level"):
        Settings(log_level="debg")


def test_settings_log_level_empty_string_rejected() -> None:
    """An empty string for log_level must be rejected."""
    with pytest.raises(ValidationError, match="log_level"):
        Settings(log_level="")


def test_settings_log_level_nonstandard_alias_rejected() -> None:
    """A non-standard alias like ``warn`` (vs. ``warning``) must be rejected."""
    with pytest.raises(ValidationError, match="log_level"):
        Settings(log_level="warn")


def test_settings_log_level_lowercase_normalized_to_uppercase() -> None:
    """Lowercase values (common in ``.env`` files) normalize to uppercase."""
    s = Settings(log_level="debug")
    assert s.log_level == "DEBUG"


def test_settings_log_level_mixed_case_normalized_to_uppercase() -> None:
    """Mixed-case input is normalized to the canonical uppercase form."""
    s = Settings(log_level="Warning")
    assert s.log_level == "WARNING"


def test_settings_log_level_uppercase_accepted() -> None:
    """Uppercase values pass through unchanged."""
    s = Settings(log_level="ERROR")
    assert s.log_level == "ERROR"


def test_settings_log_level_whitespace_stripped() -> None:
    """Leading/trailing whitespace must be stripped before validation."""
    s = Settings(log_level="  info  ")
    assert s.log_level == "INFO"


def test_settings_log_level_default_normalized_to_uppercase() -> None:
    """The default value is also normalized by the validator."""
    s = Settings()
    assert s.log_level == "INFO"


def test_settings_log_level_error_lists_valid_options() -> None:
    """Validation error must list the valid options so operators can self-correct."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(log_level="debg")
    message = str(exc_info.value)
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert level in message


# -- app_env validation (issue #370) ----------------------------------------
#
# Prior to #370, ``app_env`` was a free-form ``str`` default-init'ed to
# ``"development"``. A typoed env var such as ``APP_ENV=Production`` (wrong
# case) or ``APP_ENV=prod`` (wrong word) landed a non-``"production"`` value,
# which the ``main.py`` ``app_env == "production"`` branches treated as dev
# and exposed ``/docs``, ``/redoc``, and ``/openapi.json`` in a live prod
# deployment. Narrowing to a ``Literal`` makes Pydantic refuse unknown
# spellings at ``Settings()`` construction, turning a silent misconfiguration
# into a loud startup failure.


def test_settings_app_env_rejects_typo_casing() -> None:
    """``APP_ENV=Production`` (wrong case) must fail validation.

    Pydantic's ``Literal`` matching is case-sensitive, so a capitalised
    spelling fails fast instead of silently landing in a non-production
    branch with ``/docs`` exposed.
    """
    with pytest.raises(ValidationError, match="app_env"):
        Settings(app_env="Production")  # type: ignore[arg-type]


def test_settings_app_env_rejects_unknown_value() -> None:
    """``APP_ENV=prod`` (unknown value) must fail validation."""
    with pytest.raises(ValidationError, match="app_env"):
        Settings(app_env="prod")  # type: ignore[arg-type]


def test_settings_app_env_accepts_three_known_values() -> None:
    """The three canonical ``app_env`` values must construct without error."""
    for value in ("development", "production", "testing"):
        s = Settings(app_env=value)  # type: ignore[arg-type]
        assert s.app_env == value
