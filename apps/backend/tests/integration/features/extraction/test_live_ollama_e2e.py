"""End-to-end slow test hitting ``/api/v1/extract`` with a live Ollama/Gemma.

Issue #116: ``docs/testing.md`` advertised the slow suite as covering a
real-Ollama smoke test against the full extraction pipeline, but no such
test actually existed — the slow suite only exercised real Docling. This
module closes that gap.

The test is intentionally permissive about Gemma output content (the model
is nondeterministic across installs) but strict about response *shape*:
the endpoint must return HTTP 200 with an ``ExtractResponse`` envelope
whose ``fields`` mapping contains every declared field name from the
skill. That "every declared field always present" invariant is the
load-bearing API-stability contract and is exactly what a catastrophic
regression in the Ollama integration would break.

Skip-gate mechanism
-------------------

The test is skipped cleanly (not failed) when Ollama is not reachable at
the configured ``ollama_base_url`` OR the configured ``ollama_model`` tag
is missing from its ``/api/tags`` listing. A short, synchronous ``httpx``
probe runs once at module load to decide. This mirrors how the real-
Docling slow tests guard on ``docling`` being importable. The probe is
sync (not async) because pytest's skip decision is made at collection time,
before any event loop exists, so the simplest correct thing is
``httpx.get`` with a small timeout.

Running locally
---------------

    ollama serve &
    ollama pull gemma4:e2b   # or whatever OLLAMA_MODEL you set in .env
    # If Ollama is on localhost rather than the docker-default:
    export OLLAMA_BASE_URL=http://localhost:11434
    task test:slow
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.slow


# parents: [0]=extraction [1]=features [2]=integration [3]=tests
# so parents[3] is apps/backend/tests, which is where fixtures live.
_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "pdfs"
_FIXTURE_PDF = _FIXTURES_DIR / "native_two_page.pdf"

_DOCLING_AVAILABLE = importlib.util.find_spec("docling") is not None

_SKIP_REASON_DOCLING = (
    "docling is not installed; the live-Ollama E2E test needs real Docling "
    "parsing to feed the extraction engine."
)
_SKIP_REASON_FIXTURE = (
    "PDF fixture missing; add apps/backend/tests/fixtures/pdfs/native_two_page.pdf"
)


def _ollama_reachable(settings: Settings) -> tuple[bool, str]:
    """Probe the configured Ollama ``/api/tags`` endpoint synchronously.

    Returns ``(reachable, reason)``. ``reachable`` is ``True`` only when
    Ollama responds 200 AND the configured ``Settings.ollama_model`` tag
    appears in the ``models`` list. Any network error, non-200 response,
    malformed JSON, or missing model yields ``False`` and a human-readable
    reason that pytest surfaces as the skip message.

    Kept separate from ``OllamaHealthProbe`` because this is a synchronous,
    module-load-time check used only to make a pytest skip decision, and
    ``OllamaHealthProbe`` is async. Duplicating the ~10 lines of probing
    logic here is cheaper than spinning up an event loop at collection
    time.
    """
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        response = httpx.get(url, timeout=2.0)
    except httpx.HTTPError as exc:
        return False, f"Ollama not reachable at {url}: {type(exc).__name__}"
    if response.status_code != 200:
        return False, f"Ollama returned HTTP {response.status_code} at {url}"
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return False, f"Ollama returned non-JSON body at {url}"
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return False, f"Ollama /api/tags response missing 'models' list at {url}"
    names = [entry.get("name") for entry in models if isinstance(entry, dict)]
    if settings.ollama_model not in names:
        return (
            False,
            (
                f"Ollama model {settings.ollama_model!r} not installed; "
                f"run `ollama pull {settings.ollama_model}`. Installed: {names}"
            ),
        )
    return True, ""


# Resolve once at module load so the skip decision is deterministic for
# the whole suite. Using Settings() here reads the same env that the
# running app would, so a developer who set OLLAMA_BASE_URL=http://localhost:11434
# in their .env probes the right endpoint.
_PROBE_SETTINGS: Settings = Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
_OLLAMA_READY, _OLLAMA_SKIP_REASON = _ollama_reachable(_PROBE_SETTINGS)


def _write_invoice_skill(base: Path) -> None:
    """Write an ``invoice@1`` skill asking Gemma for the document's invoice number.

    The fixture PDF contains ``Invoice #12345`` so Gemma has a concrete
    target to latch onto. We do NOT assert the extracted value, only that
    the declared field is present in the response (API-stability contract).
    """
    body: dict[str, Any] = {
        "name": "invoice",
        "version": 1,
        "prompt": (
            "Extract the invoice number from the document. "
            "Respond with the number exactly as it appears."
        ),
        "examples": [
            {"input": "Invoice #INV-1", "output": {"number": "INV-1"}},
            {"input": "Invoice No: 98765", "output": {"number": "98765"}},
        ],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    target = base / "invoice"
    target.mkdir(parents=True, exist_ok=True)
    (target / "1.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.mark.skipif(not _DOCLING_AVAILABLE, reason=_SKIP_REASON_DOCLING)
@pytest.mark.skipif(not _FIXTURE_PDF.exists(), reason=_SKIP_REASON_FIXTURE)
@pytest.mark.skipif(not _OLLAMA_READY, reason=_OLLAMA_SKIP_REASON)
@pytest.mark.asyncio
async def test_extract_endpoint_end_to_end_against_live_ollama(tmp_path: Path) -> None:
    """Full-stack smoke test: multipart POST -> Docling -> Gemma -> response.

    Asserts the *shape* of a successful extraction, not specific Gemma
    output. The extraction service guarantees every declared field is
    present in the response — a catastrophic regression in the Ollama
    integration would break that contract, which is exactly what this
    test catches.
    """
    _write_invoice_skill(tmp_path)
    # Use whatever Settings pydantic-settings produces from the environment,
    # only overriding ``skills_dir`` to point at the ephemeral skill we just
    # wrote and ``app_env`` so ``/openapi.json`` and dev-mode behaviour stay
    # stable. This preserves the user's ``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL``
    # so the probed-reachable endpoint is the same endpoint the app will
    # hit during extraction.
    settings = Settings(  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
        skills_dir=tmp_path,
        app_env="development",
    )
    app = create_app(settings)

    # Drive lifespan explicitly so the startup probe, probe cache, and
    # provider are built and torn down cleanly — ASGITransport alone does
    # not fire lifespan events.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            pdf_bytes = _FIXTURE_PDF.read_bytes()
            response = await client.post(
                "/api/v1/extract",
                data={
                    "skill_name": "invoice",
                    "skill_version": "1",
                    "output_mode": "JSON_ONLY",
                },
                files={"pdf": ("invoice.pdf", pdf_bytes, "application/pdf")},
                timeout=120.0,  # real Gemma inference on CPU is slow
            )

    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text[:500]}"
    )
    body = response.json()

    # ExtractResponse envelope shape.
    assert body["skill_name"] == "invoice"
    assert body["skill_version"] == 1
    assert "fields" in body
    assert "metadata" in body

    # Metadata shape.
    metadata = body["metadata"]
    assert metadata["page_count"] >= 1
    assert isinstance(metadata["duration_ms"], int)
    assert metadata["duration_ms"] >= 0
    assert isinstance(metadata["attempts_per_field"], dict)

    # The declared field must always be present (API-stability invariant).
    # Whether Gemma extracts the right value is out of scope for this
    # smoke test; value correctness is covered by skill-specific eval
    # harnesses, not by the contract.
    fields = body["fields"]
    assert "number" in fields, f"declared field 'number' missing from response: {fields}"
    number_field = fields["number"]
    assert number_field["name"] == "number"
    assert number_field["status"] in {"extracted", "failed"}
    assert number_field["source"] in {"document", "inferred"}
    assert isinstance(number_field["grounded"], bool)
    assert isinstance(number_field["bbox_refs"], list)
