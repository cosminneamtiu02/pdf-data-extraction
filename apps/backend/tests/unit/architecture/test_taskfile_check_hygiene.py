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


def _collect_all_task_invocations(taskfile: dict[str, Any], root: str) -> list[str]:
    """DFS from `root` through every `task: <name>` edge; return every invocation.

    Walks the full reachable graph so depth-3+ indirections are visible — the
    depth-1 predecessor of this helper would have missed the historical
    wiring (`check` → `check:lint` → `lint` → `check:arch`) that issue #215
    closed, because `check:arch` sat at depth 3 under that shape.

    Each `task: X` edge discovered during traversal is appended to the
    result list (so duplicate references — direct + via-sibling — count as
    separate invocations, which is the thing we want to flag). The visited
    set guards against `tasks` cycles; it operates on tasks, not on edges,
    so edges through an already-walked task are still counted as invocations.
    Both `cmds:` (sequential commands) and `deps:` (parallel prerequisites)
    are traversed — either shape can reintroduce the indirection.
    """
    all_invocations: list[str] = []
    visited: set[str] = set()

    def walk(task_name: str) -> None:
        if task_name in visited:
            return
        visited.add(task_name)
        task_def = taskfile["tasks"].get(task_name, {})
        for key in ("cmds", "deps"):
            for ref in _direct_task_refs(task_def.get(key, []) or []):
                all_invocations.append(ref)
                walk(ref)

    walk(root)
    return all_invocations


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

    The walk is a full recursive DFS — not a depth-1 check — because the
    historical wiring that issue #215 closed (`check` → `check:lint` →
    `lint` → `check:arch`) placed `check:arch` at depth 3. A shallow check
    would report `arch_count == 1` even under the broken wiring (the direct
    edge alone) and miss the duplication flag's whole purpose.
    """
    taskfile = _load_taskfile()
    all_invocations = _collect_all_task_invocations(taskfile, "check")

    arch_count = all_invocations.count("check:arch")
    assert arch_count == 1, (
        f"`check:arch` must run exactly once per `task check`; got {arch_count} invocation(s). "
        f"Full task graph reachable from `check`: {all_invocations}. "
        "If this is >1, `check:arch` is both a direct cmd of `check` AND still reached via a "
        "sibling (likely `lint`, possibly at depth >= 2) — remove the sibling indirection to dedup."
    )


def test_collect_all_task_invocations_catches_depth_three_indirection() -> None:
    """Regression test: the DFS helper must see `check:arch` at depth 3.

    Pins the fix for the pre-#215 wiring shape
    `check -> check:lint -> lint -> check:arch`. A depth-1 walk (the former
    implementation of `test_check_arch_appears_exactly_once_across_check_graph`)
    would have reported `arch_count == 0` under this shape (depth-2 siblings
    have no `task:` edges to arch); the recursive DFS must report 1.
    """
    synthetic_taskfile: dict[str, Any] = {
        "tasks": {
            "check": {"cmds": [{"task": "check:lint"}]},
            "check:lint": {"cmds": [{"task": "lint"}]},
            "lint": {"cmds": [{"task": "check:arch"}]},
            "check:arch": {"cmds": ["import-linter --config architecture/..."]},
        }
    }
    invocations = _collect_all_task_invocations(synthetic_taskfile, "check")
    assert invocations.count("check:arch") == 1, (
        "DFS must detect check:arch reached at depth 3 via "
        "check -> check:lint -> lint -> check:arch. "
        f"Got invocations: {invocations}."
    )


def test_collect_all_task_invocations_counts_both_direct_and_indirect_edges() -> None:
    """DFS helper must double-count `check:arch` when it's reachable twice.

    If a future refactor accidentally keeps both the direct edge and the
    historical indirect chain, `arch_count` must be 2 so the
    `test_check_arch_appears_exactly_once_across_check_graph` assertion
    flags it.
    """
    synthetic_taskfile: dict[str, Any] = {
        "tasks": {
            "check": {"cmds": [{"task": "check:arch"}, {"task": "check:lint"}]},
            "check:lint": {"cmds": [{"task": "lint"}]},
            "lint": {"cmds": [{"task": "check:arch"}]},
            "check:arch": {"cmds": ["import-linter --config architecture/..."]},
        }
    }
    invocations = _collect_all_task_invocations(synthetic_taskfile, "check")
    assert invocations.count("check:arch") == 2, (
        "DFS must count every `task: check:arch` edge it encounters, "
        f"including edges through already-visited tasks. Got: {invocations}."
    )


def test_collect_all_task_invocations_handles_cycles_without_infinite_loop() -> None:
    """DFS must terminate even when the task graph has a cycle.

    Taskfile doesn't currently have cycles, but a future mistake could
    introduce one. The visited-set guard is the load-bearing invariant
    here — without it the DFS would recurse forever and the test would
    hang rather than fail cleanly.
    """
    synthetic_taskfile: dict[str, Any] = {
        "tasks": {
            "check": {"cmds": [{"task": "a"}]},
            "a": {"cmds": [{"task": "b"}]},
            "b": {"cmds": [{"task": "a"}]},
        }
    }
    invocations = _collect_all_task_invocations(synthetic_taskfile, "check")
    # The `a -> b -> a` cycle means we visit each task once, seeing each
    # edge at most once per parent; cycle should not cause unbounded growth.
    assert "a" in invocations
    assert "b" in invocations
    assert len(invocations) < 10, (
        f"DFS produced {len(invocations)} invocations on a 3-node cycle — likely unbounded."
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
