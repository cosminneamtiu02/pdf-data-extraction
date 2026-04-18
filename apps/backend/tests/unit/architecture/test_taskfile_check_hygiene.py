"""Hygiene checks on the `check` task's `cmds:` list in `Taskfile.yml`.

Sacred Rule #4 in CLAUDE.md: "Run `task check` before declaring any work
done." The canonical gate must therefore enumerate every contract we care
about directly, not reach them transitively through sibling tasks.

Before issue #215, `check:arch` (import-linter) was only reached via
`check:lint -> lint -> check:arch`. That coupling was invisible when
reading the `check` task in isolation and would have silently fallen out
of the canonical gate if a future refactor had split `lint` into
ruff-only and architecture tasks. These tests pin the invariant that
`check` enumerates every required gate — lint, types, arch, skills,
tests, errors — as a direct `task: <name>` command, and that each gate
runs exactly once (no duplication via a sibling chain).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, cast

import yaml

from ._linter_subprocess import REPO_ROOT

_TASKFILE: Final[Path] = REPO_ROOT / "Taskfile.yml"


_REQUIRED_DIRECT_GATES: Final[tuple[str, ...]] = (
    "check:lint",
    "check:types",
    "check:arch",
    "skills:validate",
    "check:test",
    "check:errors",
)


def _load_taskfile() -> dict[str, Any]:
    raw = _TASKFILE.read_text(encoding="utf-8")
    return cast("dict[str, Any]", yaml.safe_load(raw))


def _direct_task_refs(cmds: list[Any]) -> list[str]:
    """Return the names of `task: <name>` entries in a Taskfile `cmds:` list.

    Each `task: <name>` entry is a YAML mapping `{"task": "<name>"}`. Shell
    commands are plain strings and are ignored for this assertion — only
    direct task-dependency edges count as gates.
    """
    return [str(entry["task"]) for entry in cmds if isinstance(entry, dict) and "task" in entry]


def test_check_task_lists_every_gate_as_direct_dependency() -> None:
    """The `check` task's `cmds:` must name every required gate directly."""
    check_cmds = _load_taskfile()["tasks"]["check"]["cmds"]
    direct_refs = _direct_task_refs(check_cmds)

    missing = [gate for gate in _REQUIRED_DIRECT_GATES if gate not in direct_refs]
    assert not missing, (
        "`check` task must enumerate every contract gate directly in its `cmds:` list. "
        f"Missing: {missing}. Found direct refs: {direct_refs}. "
        "Reaching a gate only via a sibling task (e.g. `check:arch` via `lint`) is forbidden "
        "by issue #215 — a future refactor of the sibling would silently drop the gate."
    )


def test_check_arch_appears_exactly_once_across_check_graph() -> None:
    """`check:arch` must run exactly once under `task check` — no duplication.

    If we add `check:arch` directly to `check` without also removing the
    transitive edge through `lint`, import-linter would execute twice per
    `task check` invocation. Enforce the dedup explicitly.
    """
    taskfile = _load_taskfile()
    check_cmds = taskfile["tasks"]["check"]["cmds"]
    direct_refs = _direct_task_refs(check_cmds)

    # Walk one level of task: <name> indirection so we also see gates reached
    # via check:lint, check:test, check:errors, etc.
    all_invocations: list[str] = list(direct_refs)
    for ref in direct_refs:
        sibling = taskfile["tasks"].get(ref, {})
        sibling_cmds = sibling.get("cmds", [])
        all_invocations.extend(_direct_task_refs(sibling_cmds))

    arch_count = all_invocations.count("check:arch")
    assert arch_count == 1, (
        f"`check:arch` must run exactly once per `task check`; got {arch_count} invocation(s). "
        f"Full task graph (one level deep): {all_invocations}. "
        "If this is >1, `check:arch` is both a direct cmd of `check` AND still reached via a "
        "sibling (likely `lint`) — remove the sibling indirection to dedup."
    )


def test_lint_task_is_ruff_only_no_arch_indirection() -> None:
    """`lint` must handle ruff only — architecture belongs to `check:arch` directly.

    This pairs with the `check_arch_appears_exactly_once` invariant: the
    simplest way to keep that invariant is to make `lint` a pure ruff
    runner and wire `check:arch` as an explicit top-level command of
    `check`. Any sibling task that recursively calls `check:arch` would
    resurrect the indirection issue #215 closed.
    """
    lint_cmds = _load_taskfile()["tasks"]["lint"].get("cmds", [])
    lint_task_refs = _direct_task_refs(lint_cmds)
    assert "check:arch" not in lint_task_refs, (
        "`lint` must not delegate to `check:arch`. "
        "Architecture is a direct dependency of `check` per issue #215; "
        f"found lint task: refs: {lint_task_refs}."
    )
