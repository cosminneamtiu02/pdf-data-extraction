"""Shared fixtures for the contract-test suite — issue #352.

Houses the session-scoped ``extract_schema`` fixture that builds the
schemathesis ``BaseSchema`` against a throwaway FastAPI app. The
schema is loaded exactly once per pytest session rather than once per
test (or, worse, at module import time — the pre-#352 behavior that
leaked a ``tempfile.mkdtemp`` skills directory on every invocation,
including bare ``--collect-only`` runs).

The loader is a ``@contextmanager`` named ``_build_extract_schema_session``
so the session-scoped fixture is a thin three-line wrapper and the
cleanup contract is also directly exercisable from
``tests/unit/meta/test_contract_schema_fixture_cleanup.py``. Driving
the context manager directly (rather than through ``pytest.Pytester``)
keeps the meta-test fast and avoids coupling the finalizer-leak probe
to pytest's own ``tmp_path_factory`` retention policy.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import schemathesis

from app.main import create_app
from tests.contract._helpers import settings as _settings
from tests.contract._helpers import write_valid_skill as _write_valid_skill

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def _build_extract_schema_session() -> Iterator[tuple[schemathesis.BaseSchema, Path]]:
    """Build the schemathesis schema against a throwaway app, with guaranteed cleanup.

    Yields ``(schema, skills_dir)`` so callers (the session fixture *and*
    the meta-test) can both use the schema and inspect the skills
    directory that backs it.

    Uses ``tempfile.TemporaryDirectory`` rather than pytest's
    ``tmp_path_factory`` to get deterministic ``__exit__`` cleanup.
    ``tmp_path_factory`` retains the last three session roots by
    default, which is fine for test debuggability but would make the
    "tempdir is gone after the yield" contract non-load-bearing — the
    directory would still be on disk until pytest rotated it out.
    Explicit ``TemporaryDirectory()`` removes the directory
    unconditionally the moment the ``with`` block exits, so the
    cleanup assertion in the meta-test is a real probe.

    The schema is built against a dedicated ``create_app(Settings(...))``
    rather than the process-wide ``app``: if the ambient environment
    has ``APP_ENV=production``, ``create_app`` disables
    ``/openapi.json`` and ``from_asgi`` would 404. Pinning
    ``app_env="development"`` via ``_settings`` (see
    ``tests/contract/_helpers.py``) makes the fixture robust to
    ambient env vars.
    """
    with tempfile.TemporaryDirectory(prefix="pdfx_contract_extract_schema_") as tmpdir:
        skills_dir = Path(tmpdir)
        # `create_app` validates skill YAMLs at startup, so a minimal
        # valid `invoice@1` must exist before `from_asgi` spins the app.
        _write_valid_skill(skills_dir)
        schema_app = create_app(_settings(skills_dir))
        # `openapi.from_asgi` uses `starlette_testclient.TestClient`
        # synchronously under the hood; running this inside an
        # `async def` test would deadlock against the outer
        # pytest-asyncio loop. Session-scope + sync loader is the
        # supported pattern.
        schema = schemathesis.openapi.from_asgi("/openapi.json", schema_app)
        yield schema, skills_dir


@pytest.fixture(scope="session")
def extract_schema() -> Iterator[schemathesis.BaseSchema]:
    """Session-scoped schemathesis schema for ``POST /api/v1/extract``.

    Loaded exactly once per session and shared across every contract
    test that needs ``validate_response``. The OpenAPI contract for
    ``/api/v1/extract`` is identical across the per-test ``Settings``
    variations the test apps use (declared response shapes don't
    depend on runtime config), so one session-scoped schema is correct.
    """
    with _build_extract_schema_session() as (schema, _skills_dir):
        yield schema
