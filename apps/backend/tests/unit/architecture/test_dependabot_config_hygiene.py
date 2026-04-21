"""Hygiene checks for `.github/dependabot.yml` grouping invariants.

`docs/automerge.md` Incident 3 documents the rebase-conflict cascade that
happens when sibling Dependabot PRs touch adjacent lines in the same manifest
file. The permanent cure is to put interlocking packages into a single
`groups:` entry so their bumps land as one atomic PR.

The Docling ML cluster (`docling`, `torch`, `torchvision`) lives on adjacent
lines in `apps/backend/pyproject.toml` and moves in lockstep per ADR-012
(CPU-only torch wheels on Linux). Without a group, a sequence that bumps
`docling` first and `torch` second is the exact Incident 3 pattern. This
meta-test pins the group so a future refactor cannot silently drop it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from ._linter_subprocess import REPO_ROOT

_DEPENDABOT_CONFIG: Final[Path] = REPO_ROOT / ".github" / "dependabot.yml"
_BACKEND_DIRECTORY: Final[str] = "/apps/backend"
_DOCLING_STACK_GROUP_NAME: Final[str] = "docling-stack"
_DOCLING_STACK_REQUIRED_PATTERNS: Final[frozenset[str]] = frozenset(
    {"docling", "torch", "torchvision"}
)


def _load_dependabot_config() -> dict[str, Any]:
    """Parse `.github/dependabot.yml` and return the top-level mapping."""
    loaded = yaml.safe_load(_DEPENDABOT_CONFIG.read_text())
    assert isinstance(loaded, dict), (
        f"{_DEPENDABOT_CONFIG} did not parse to a YAML mapping — "
        "got a non-mapping or null value, likely malformed YAML."
    )
    return loaded


def _find_backend_pip_update(config: dict[str, Any]) -> dict[str, Any]:
    """Return the pip-ecosystem `updates:` entry scoped to `/apps/backend`."""
    updates = config.get("updates", [])
    assert isinstance(updates, list), (
        "`updates:` in dependabot.yml must be a list of ecosystem entries."
    )
    for entry in updates:
        if not isinstance(entry, dict):
            continue
        if entry.get("package-ecosystem") == "pip" and entry.get("directory") == _BACKEND_DIRECTORY:
            return entry
    msg = (
        f"no pip ecosystem entry for directory {_BACKEND_DIRECTORY!r} found "
        "in dependabot.yml — this should never happen."
    )
    raise AssertionError(msg)


def test_docling_stack_group_batches_docling_torch_torchvision() -> None:
    """The `docling-stack` group must batch docling + torch + torchvision.

    See docs/automerge.md Incident 3 and ADR-012 for the rationale. These
    three packages sit on adjacent lines in `apps/backend/pyproject.toml`
    and move in lockstep; without a group, the weekly bump sequence is the
    exact rebase-conflict cascade pattern Incident 3 describes.
    """
    config = _load_dependabot_config()
    backend_pip = _find_backend_pip_update(config)

    groups = backend_pip.get("groups", {})
    assert isinstance(groups, dict), (
        "`groups:` under the backend pip ecosystem must be a mapping of group-name -> group-config."
    )

    assert _DOCLING_STACK_GROUP_NAME in groups, (
        f"the backend pip ecosystem must define a `{_DOCLING_STACK_GROUP_NAME}` "
        "group that batches docling + torch + torchvision into a single "
        "atomic Dependabot PR (docs/automerge.md Incident 3, ADR-012). "
        f"Got groups: {sorted(groups.keys())!r}"
    )

    docling_group = groups[_DOCLING_STACK_GROUP_NAME]
    assert isinstance(docling_group, dict), (
        f"`{_DOCLING_STACK_GROUP_NAME}` group must be a mapping with a `patterns:` list."
    )

    patterns = docling_group.get("patterns", [])
    assert isinstance(patterns, list), (
        f"`{_DOCLING_STACK_GROUP_NAME}.patterns` must be a list of package globs."
    )

    missing = _DOCLING_STACK_REQUIRED_PATTERNS - set(patterns)
    assert not missing, (
        f"`{_DOCLING_STACK_GROUP_NAME}` group is missing required "
        f"patterns: {sorted(missing)!r}. The group must batch all three of "
        f"{sorted(_DOCLING_STACK_REQUIRED_PATTERNS)!r} so their bumps move "
        "as a single atomic PR (docs/automerge.md Incident 3)."
    )
