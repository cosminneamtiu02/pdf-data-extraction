"""Taskfile hygiene guardrails for timeouts, non-mutating checks, and codegen fingerprints.

Issue #357 closes three gaps in `Taskfile.yml`:

1. **Timeouts.** Long-running tasks (test suites, type checking, full
   `check`) had no upper time bound, so a hung subprocess would stall
   indefinitely. Tests assert every long-running task wraps its real
   command in `infra/taskfile/with_timeout.py <seconds>`, which
   terminates the subprocess after the given deadline.

2. **Non-mutating `errors:check`.** The previous `errors:check` shape
   ran `errors:generate` (mutating the working tree) and then diffed
   the result. Tests assert `errors:check` does not invoke
   `errors:generate`, does not shell out to the generator's
   `generate_python` / `generate_typescript` / `generate_required_keys`
   functions with the live destination paths, and instead calls the
   read-only `scripts.check` driver that generates into a temp dir.

3. **Codegen `sources:` + `generates:` hashing.** Taskfile can skip a
   task when its declared `sources:` checksum matches the prior run.
   Tests assert every codegen task declares both `sources:` (at least
   the YAML source of truth) and `generates:` (at least one of the
   destination paths); without them, `task errors:generate` re-runs
   the generator on every invocation instead of no-op'ing when nothing
   changed.

These are pure YAML-parse + string-match tests — no subprocesses, no
`task` binary dependency, no disk mutation. They run under the regular
unit-test suite and fail fast if a future Taskfile edit silently drops
any of the three guardrails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, cast

import yaml

# _REPO_ROOT: tests/unit/meta/<file> -> meta -> unit -> tests -> backend -> apps -> repo
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_TASKFILE: Final[Path] = _REPO_ROOT / "Taskfile.yml"
_TIMEOUT_WRAPPER: Final[Path] = _REPO_ROOT / "infra" / "taskfile" / "with_timeout.py"


# Tasks that *must* carry a timeout wrapper. Any task that shells out
# to pytest / pyright / ruff / uv sync / docker build / the LLM
# validator belongs here — anything that could legitimately take more
# than a few seconds and that a hung subprocess could stall forever.
_TASKS_REQUIRING_TIMEOUT: Final[tuple[str, ...]] = (
    "check",
    "check:lint",
    "check:types",
    "check:types:backend",
    "check:types:error-contracts",
    "check:arch",
    "check:test",
    "check:errors",
    "test",
    "test:unit",
    "test:integration",
    "test:contract",
    "test:slow",
    "lint",
    "format:check",
    "skills:validate",
    "docker:build",
    "errors:test",
    "errors:check",
    "errors:generate",
)


# Codegen tasks that must declare sources: + generates: so Task can
# skip a no-op re-run based on checksum comparison.
_CODEGEN_TASKS: Final[tuple[str, ...]] = ("errors:generate",)


def _load_taskfile() -> dict[str, Any]:
    raw = _TASKFILE.read_text(encoding="utf-8")
    return cast("dict[str, Any]", yaml.safe_load(raw))


def _shell_cmds(task_def: dict[str, Any]) -> list[str]:
    """Return the raw shell-string entries from a task's `cmds:` list.

    Task's `cmds:` can hold either plain strings (shell commands), or
    mappings like `{"task": "other-task"}` or `{"cmd": "...", "ignore_error": true}`.
    This helper flattens to the string payload: shell strings stay as-is,
    `{"cmd": "..."}` mappings unwrap to their cmd value, and `{"task": ...}`
    references are dropped (they delegate to a sibling task whose timeout
    is checked independently). Includes `defer:` payloads too since they
    run inside the same task's process and should be bounded the same way.
    """
    shell: list[str] = []
    for entry in task_def.get("cmds", []) or []:
        if isinstance(entry, str):
            shell.append(entry)
        elif isinstance(entry, dict):
            if "cmd" in entry:
                shell.append(str(entry["cmd"]))
            elif "defer" in entry:
                shell.append(str(entry["defer"]))
    return shell


def test_timeout_wrapper_script_exists() -> None:
    """`infra/taskfile/with_timeout.py` is load-bearing for every timeout assertion.

    Without the wrapper file, the per-task timeout invocations below
    would resolve to nothing at run-time — Task would fail with
    "file not found" only when a human ran `task check` locally, long
    after a PR had merged. Check it exists as a file, not just a
    string in the Taskfile.
    """
    assert _TIMEOUT_WRAPPER.is_file(), (
        f"expected timeout wrapper at {_TIMEOUT_WRAPPER} — every long-running task "
        f"in Taskfile.yml references it via `{{{{.ROOT_DIR}}}}/infra/taskfile/with_timeout.py`."
    )


# The wrapper is referenced from Taskfile commands either as the
# literal filename (`with_timeout.py`) or via the repo-level alias
# variable `{{.TIMEOUT_WRAPPER}}` declared in the Taskfile's top-level
# `vars:` block. Either form satisfies the "wrapped" contract.
_TIMEOUT_MARKERS: Final[tuple[str, ...]] = ("with_timeout.py", "{{.TIMEOUT_WRAPPER}}")


def _shell_cmd_has_timeout_marker(shell_cmd: str) -> bool:
    return any(marker in shell_cmd for marker in _TIMEOUT_MARKERS)


def test_taskfile_declares_timeout_wrapper_variable() -> None:
    """Taskfile.yml must declare a `TIMEOUT_WRAPPER` var aliasing the wrapper path.

    The alias exists so every shell command can reference the wrapper
    via `{{.TIMEOUT_WRAPPER}}` rather than repeating the full
    `{{.ROOT_DIR}}/infra/taskfile/with_timeout.py` path on every line —
    that repetition would be rename-fragile if the wrapper ever moved.
    """
    taskfile = _load_taskfile()
    vars_block = taskfile.get("vars") or {}
    raw = str(vars_block.get("TIMEOUT_WRAPPER", ""))
    assert raw, (
        "Taskfile.yml must declare `vars.TIMEOUT_WRAPPER` aliasing "
        "`{{.ROOT_DIR}}/infra/taskfile/with_timeout.py`."
    )
    assert "with_timeout.py" in raw, (
        f"`vars.TIMEOUT_WRAPPER` must point at the wrapper script; got {raw!r}."
    )


def test_every_long_running_task_wraps_shell_cmds_with_timeout() -> None:
    """Each task in `_TASKS_REQUIRING_TIMEOUT` must wrap its shell commands in with_timeout.py.

    Shell commands (plain strings or `{"cmd": "..."}` mappings) are the
    ones that spawn real work. Pure `task: <name>` references delegate
    to a sibling whose timeout is enforced independently, so they are
    exempt from this check — the sibling's shell commands carry the
    real timeout.
    """
    taskfile = _load_taskfile()
    offenders: list[str] = []
    for task_name in _TASKS_REQUIRING_TIMEOUT:
        task_def = taskfile["tasks"].get(task_name)
        assert task_def is not None, f"`{task_name}` must exist in Taskfile.yml"
        offenders.extend(
            f"{task_name}: {shell_cmd!r}"
            for shell_cmd in _shell_cmds(task_def)
            if not _shell_cmd_has_timeout_marker(shell_cmd)
        )
    assert not offenders, (
        "Every shell command in a long-running task must wrap its real command via "
        "`python3 {{.TIMEOUT_WRAPPER}} <seconds> -- <cmd>` "
        "(or the equivalent literal `with_timeout.py` path). "
        f"Offenders (task: cmd): {offenders}"
    )


def test_timeout_values_are_positive_integers() -> None:
    """Shell commands that invoke with_timeout.py must pass a positive integer seconds value.

    The wrapper rejects non-int or non-positive deadlines at parse
    time, but a typo caught in CI seconds after a merge is worth it —
    catch it at static-parse time here. Regex-extract the token
    immediately after `with_timeout.py` and assert it parses as a
    positive int.
    """
    import re

    taskfile = _load_taskfile()
    # Match either the literal `with_timeout.py` path or the Taskfile
    # alias variable `{{.TIMEOUT_WRAPPER}}` — both resolve to the
    # wrapper binary and accept the seconds value as the next token.
    pattern = re.compile(r"(?:with_timeout\.py|\{\{\.TIMEOUT_WRAPPER\}\})\s+(\S+)")
    offenders: list[str] = []
    for task_name in _TASKS_REQUIRING_TIMEOUT:
        task_def = taskfile["tasks"].get(task_name, {})
        for shell_cmd in _shell_cmds(task_def):
            match = pattern.search(shell_cmd)
            if match is None:
                # The previous test already flags the missing-wrapper
                # case; don't double-report it here.
                continue
            raw = match.group(1)
            try:
                seconds = int(raw)
            except ValueError:
                offenders.append(f"{task_name}: non-integer timeout token {raw!r}")
                continue
            if seconds <= 0:
                offenders.append(f"{task_name}: non-positive timeout {seconds}")
    assert not offenders, (
        "with_timeout.py's first argument must be a positive integer (seconds). "
        f"Offenders: {offenders}"
    )


def test_errors_check_is_non_mutating() -> None:
    """`errors:check` must not invoke `errors:generate` and must not shell out
    to the generator entry points with the live destination paths.

    The non-mutating contract (issue #291) says `errors:check`
    verifies parity by generating into a temp dir and byte-comparing
    against the live paths. That's what `scripts.check.run_check` does.
    Calling `errors:generate` instead would write directly to the live
    paths and leave the working tree dirty — the failure mode this
    task closed.
    """
    taskfile = _load_taskfile()
    check_def = taskfile["tasks"].get("errors:check")
    assert check_def is not None, "`errors:check` must exist"

    # Rule 1: no direct `task: errors:generate` dependency edge.
    for entry in check_def.get("cmds", []) or []:
        if isinstance(entry, dict) and entry.get("task") == "errors:generate":
            msg = (
                "`errors:check` must not delegate to `errors:generate` (mutating). "
                "Call `scripts.check` which generates into a temp dir and diffs."
            )
            raise AssertionError(msg)

    # Rule 2: no shell command writes to the live python/ts/json paths.
    # Signature of the mutating pattern: invoking generate_python /
    # generate_typescript / generate_required_keys with an absolute or
    # relative path that lands inside apps/backend/app/exceptions/_generated,
    # src/generated.ts, or src/required-keys.json.
    forbidden_generators = (
        "generate_python",
        "generate_typescript",
        "generate_required_keys",
    )
    for shell_cmd in _shell_cmds(check_def):
        for symbol in forbidden_generators:
            if symbol in shell_cmd:
                msg = (
                    f"`errors:check` shell command references the mutating generator "
                    f"`{symbol}` — use `scripts.check` (non-mutating) instead. "
                    f"Offending cmd: {shell_cmd!r}"
                )
                raise AssertionError(msg)


def test_errors_check_invokes_the_non_mutating_check_driver() -> None:
    """`errors:check` must call the `scripts.check` module (non-mutating driver).

    This is the positive assertion paired with
    `test_errors_check_is_non_mutating` — not only must it avoid the
    mutating generators, it must also positively call the read-only
    check driver. Without this, a future refactor could drop the
    invocation entirely and the task would silently pass as a no-op.
    """
    taskfile = _load_taskfile()
    check_def = taskfile["tasks"]["errors:check"]
    joined = " ".join(_shell_cmds(check_def))
    assert "scripts.check" in joined, (
        "`errors:check` must invoke the non-mutating `scripts.check` module. "
        f"Found cmds: {_shell_cmds(check_def)!r}"
    )


def test_codegen_tasks_declare_sources_and_generates() -> None:
    """Every codegen task in `_CODEGEN_TASKS` must declare both sources: and generates:.

    Without `sources:`, Task cannot compute a checksum to know when to
    skip. Without `generates:`, Task cannot verify the outputs exist
    and will re-run even when the outputs are already up to date.
    Both are required for the incremental-build contract (go-task docs:
    https://taskfile.dev/docs/reference/schema#task).
    """
    taskfile = _load_taskfile()
    offenders: list[str] = []
    for task_name in _CODEGEN_TASKS:
        task_def = taskfile["tasks"].get(task_name)
        assert task_def is not None, f"`{task_name}` must exist in Taskfile.yml"
        if not task_def.get("sources"):
            offenders.append(f"{task_name}: missing `sources:`")
        if not task_def.get("generates"):
            offenders.append(f"{task_name}: missing `generates:`")
    assert not offenders, (
        "Every codegen task must declare `sources:` + `generates:` so Task can "
        f"skip no-op re-runs. Offenders: {offenders}"
    )


def test_errors_generate_sources_include_errors_yaml() -> None:
    """`errors:generate`'s `sources:` must include `errors.yaml` — the source of truth.

    If the YAML is not in `sources:`, editing the YAML would not bust
    the fingerprint, and `task errors:generate` would report "up to
    date" against stale outputs. Catch that specific omission.
    """
    taskfile = _load_taskfile()
    sources = taskfile["tasks"]["errors:generate"].get("sources") or []
    assert any("errors.yaml" in str(s) for s in sources), (
        f"`errors:generate`'s `sources:` must include `errors.yaml`. Got: {sources!r}"
    )


def test_errors_generate_generates_points_to_live_outputs() -> None:
    """`errors:generate`'s `generates:` must point to at least one real output path.

    The three output kinds are:
      - the Python _generated package tree
      - the TypeScript generated.ts
      - the required-keys.json
    At least one of those must appear so Task has a concrete output
    fingerprint to check.
    """
    taskfile = _load_taskfile()
    generates = taskfile["tasks"]["errors:generate"].get("generates") or []
    joined = " ".join(str(g) for g in generates)
    assert "_generated" in joined or "generated.ts" in joined or "required-keys.json" in joined, (
        "`errors:generate`'s `generates:` must reference at least one of the "
        "live output paths (_generated/, generated.ts, required-keys.json). "
        f"Got: {generates!r}"
    )
