"""Repo-root path helper for the infra hygiene test tree (issue #400).

These static hygiene tests target files outside `apps/backend/` —
`infra/docker/backend.Dockerfile`, `.github/workflows/*.yml`, and
`.github/dependabot.yml`. They used to live under
`apps/backend/tests/unit/architecture/` purely for convenience (that is
where `task check` runs), but that placement forced backend developers
to understand Docker and GitHub Actions YAML to debug unit-test
failures and violated the "tests/unit/ mirrors app/" invariant. Issue
#400 relocated them to `infra/tests/hygiene/` so they live next to the
infrastructure they assert on.

`REPO_ROOT` is computed from this file's own location: `_paths.py` ->
`hygiene/` -> `tests/` -> `infra/` -> repo root, i.e. three `.parents`
hops up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# parents[3] resolves the repo root by walking three levels up from this file:
# _paths.py -> hygiene/ -> tests/ -> infra/ -> repo
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
