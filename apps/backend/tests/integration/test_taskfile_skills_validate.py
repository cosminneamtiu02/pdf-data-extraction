"""Integration tests for the `task skills:validate` Taskfile wiring.

CI does not install the `task` binary — these tests verify the Taskfile.yml
declaration shape instead of shelling out to `task`. The subprocess-level
behavior of the script itself is covered by the unit tests in
`tests/unit/scripts/test_validate_skills.py` (which run it via `python -m`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

# `apps/backend/tests/integration/...` → repo root is five parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_TASKFILE = _REPO_ROOT / "Taskfile.yml"


def _load_taskfile() -> dict[str, Any]:
    raw = _TASKFILE.read_text(encoding="utf-8")
    return cast("dict[str, Any]", yaml.safe_load(raw))


def test_taskfile_defines_skills_validate_target() -> None:
    tasks = _load_taskfile()["tasks"]
    assert "skills:validate" in tasks, "skills:validate target missing from Taskfile.yml"


def test_skills_validate_has_real_description_not_stub() -> None:
    target = _load_taskfile()["tasks"]["skills:validate"]
    desc = str(target.get("desc", ""))
    assert desc, "skills:validate must declare a desc for `task --list` output"
    assert "STUB" not in desc, "skills:validate desc still advertises the pre-F004 stub"
    assert "FAILS" not in desc.upper()


def test_skills_validate_invokes_the_module_entry_point() -> None:
    target = _load_taskfile()["tasks"]["skills:validate"]
    cmds = target.get("cmds", [])
    joined = " ".join(str(c) for c in cmds)
    assert "scripts.validate_skills" in joined, (
        "skills:validate must invoke `python -m scripts.validate_skills`"
    )
    assert "uv run" in joined, "skills:validate must run under `uv run`"


def test_skills_validate_runs_in_backend_dir() -> None:
    taskfile = _load_taskfile()
    target = taskfile["tasks"]["skills:validate"]
    dir_value = str(target.get("dir", ""))
    backend_var = str(taskfile.get("vars", {}).get("BACKEND_DIR", ""))
    # Accept either a literal backend path or the Taskfile variable reference
    # that resolves to it. Both are valid wirings for this target.
    resolved = dir_value.endswith("backend") or (
        dir_value == "{{.BACKEND_DIR}}" and backend_var.endswith("backend")
    )
    assert resolved, (
        f"skills:validate must run from apps/backend; got dir={dir_value!r}, "
        f"BACKEND_DIR={backend_var!r}"
    )


def test_check_target_includes_skills_validate_pre_step() -> None:
    check_cmds = _load_taskfile()["tasks"]["check"]["cmds"]
    referenced = [
        str(entry.get("task", "")) if isinstance(entry, dict) else "" for entry in check_cmds
    ]
    assert "skills:validate" in referenced, (
        "`task check` must include skills:validate as a pre-step per PDFX-E002-F004 scope"
    )
