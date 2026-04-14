"""Unit tests for the IntelligenceUnavailableError placeholder exception.

The error is a plain Python exception — no DomainError machinery until
PDFX-E004-F004 wires `INTELLIGENCE_UNAVAILABLE` into `errors.yaml`. These
tests pin the minimal contract the provider relies on: a message and an
optional `cause` attribute for log context.
"""

from __future__ import annotations

from app.features.extraction.intelligence.intelligence_unavailable_error import (
    IntelligenceUnavailableError,
)


def test_error_without_cause_stores_message_and_none_cause() -> None:
    err = IntelligenceUnavailableError("ollama unreachable")
    assert str(err) == "ollama unreachable"
    assert err.cause is None


def test_error_with_cause_preserves_original_exception() -> None:
    original = ConnectionRefusedError("refused")
    err = IntelligenceUnavailableError("ollama unreachable", cause=original)
    assert err.cause is original
