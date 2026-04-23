"""Integration test: ``scripts/benchmark.py`` passes ``pyright --strict``.

Relocated from ``tests/unit/scripts/test_benchmark.py`` per issue #398: the
assertion requires spawning a full ``pyright`` subprocess, which cold-starts
in 5-15 s because pyright walks the project's reachability graph. That
magnitude of wall time blows the sacred <10 s unit-suite budget enshrined in
CLAUDE.md's Testing Rules, so the test must live outside the unit layer.

Integration was chosen over architecture because the architecture suite sits
under ``tests/unit/architecture/`` and is executed as part of ``test:unit``,
inheriting the same <10 s budget. Only ``tests/integration/`` (600 s budget
under ``test:integration`` in ``Taskfile.yml``) accommodates a ~10 s pyright
subprocess without risking a suite-wide timeout.

The test is intentionally NOT marked ``slow``: ``test:integration`` runs with
``-m "not slow"``, which is exactly the gate we want this pin to ride on so
``task check`` keeps covering the benchmark module specifically. Sibling
``test_benchmark.py`` in this directory is ``slow``-marked (runs a real
uvicorn server) and therefore excluded from ``task check`` by design — this
file is deliberately separate so its single fast subprocess test does not
inherit that marker.

Redundancy note: ``task check:types:backend`` already runs ``pyright
app/ scripts/ tests/`` across the whole backend, so the benchmark module is
type-checked there too. This test is an explicit, grep-able pin that the
benchmark module in particular stays strict-clean as it evolves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_pyright_strict_passes_on_benchmark_module() -> None:
    """pyright --strict reports zero errors on scripts/benchmark.py."""
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--pythonversion", "3.13", "scripts/benchmark.py"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[3]),  # apps/backend/
    )
    assert result.returncode == 0, f"pyright errors:\n{result.stdout}\n{result.stderr}"
