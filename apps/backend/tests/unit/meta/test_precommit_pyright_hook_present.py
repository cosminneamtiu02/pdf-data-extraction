"""Guardrail: `.pre-commit-config.yaml` must include a pre-push pyright hook
that invokes pyright through `uv run` (so the pinned version is sourced from
each subproject's `uv.lock`, NOT a separate pre-commit `rev:` that can drift).

Why:

- Issue #360: the repo historically had a ruff drift guard but no pyright
  guard. Pyright was absent from pre-commit altogether, so CI could fail
  for type errors that `task check:types` catches but no pre-commit stage
  surfaces. Adding a pyright pre-push hook closes the gap.
- Issue #453: the original `RobertCraigie/pyright-python` hook ran pyright
  from the repo root with no venv context and failed with
  `reportMissingImports` for every third-party import — the monorepo's
  dependencies live under `apps/backend/.venv` and
  `packages/error-contracts/.venv`, and the external-repo hook has no way
  to select them. The fix swaps it for a `repo: local` hook that invokes
  `uv run pyright` per subproject, mirroring `task check:types`. `uv run`
  automatically selects the correct venv, so the pyright version is
  inherited from each subproject's `uv.lock` — there is no separate
  pre-commit `rev:` to drift.

When this test fails, pick one of:

- The `.pre-commit-config.yaml` has no hook with `id: pyright` at the
  pre-push stage — add a `repo: local` entry that runs `uv run pyright`
  against at least the backend subproject.
- The entry stopped going through `uv run`, which would let pyright
  resolve outside the pinned lockfile version. Restore `uv run pyright`
  in the entry.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[5]
_PRECOMMIT_PATH = _REPO_ROOT / ".pre-commit-config.yaml"

_PYRIGHT_HOOK_ID = "pyright"
_PRE_PUSH_STAGE = "pre-push"
_UV_RUN_PYRIGHT = "uv run pyright"


def _precommit_pyright_hook() -> dict[str, object]:
    """Return the pre-commit hook dict with id=pyright from any repo entry.

    AssertionError (not TypeError) is the intended failure shape: this is a
    pytest guardrail helper, and pytest's test-runner messaging is keyed on
    AssertionError for test-assertion-style reporting. The ``noqa: TRY004``
    suppressions are scoped to ``raise AssertionError`` sites that directly
    follow ``isinstance(...)`` checks.
    """
    data = yaml.safe_load(_PRECOMMIT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = (
            f"{_PRECOMMIT_PATH} did not parse to a mapping "
            f"(got {type(data).__name__}); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        msg = (
            f"{_PRECOMMIT_PATH} top-level 'repos' is {type(repos).__name__!r} "
            f"(expected list); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        hooks = repo.get("hooks", [])
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            if hook.get("id") == _PYRIGHT_HOOK_ID:
                return hook
    msg = (
        f"No hook with id={_PYRIGHT_HOOK_ID!r} found in {_PRECOMMIT_PATH}; "
        f"add a `repo: local` entry that runs `{_UV_RUN_PYRIGHT}` for the "
        f"backend subproject (issues #360, #453)."
    )
    raise AssertionError(msg)


def test_precommit_has_pyright_hook_at_pre_push() -> None:
    hook = _precommit_pyright_hook()
    stages = hook.get("stages")
    stages_list = stages if isinstance(stages, list) else []
    missing_msg = (
        f"pyright hook in {_PRECOMMIT_PATH} must run at stage {_PRE_PUSH_STAGE!r} "
        f"(got stages={stages!r}). Issue #360."
    )
    assert _PRE_PUSH_STAGE in stages_list, missing_msg


def test_precommit_pyright_entry_goes_through_uv_run() -> None:
    hook = _precommit_pyright_hook()
    entry = hook.get("entry")
    if not isinstance(entry, str):
        msg = (
            f"pyright hook in {_PRECOMMIT_PATH} is missing a string 'entry' "
            f"(got {entry!r}); cannot verify it routes through {_UV_RUN_PYRIGHT!r}."
        )
        raise AssertionError(msg)  # noqa: TRY004
    drift_msg = (
        f"pyright hook in {_PRECOMMIT_PATH} must invoke pyright through "
        f"`{_UV_RUN_PYRIGHT}` so the pinned version comes from each "
        f"subproject's uv.lock (not a separate pre-commit rev). "
        f"Got entry={entry!r}. Issue #453."
    )
    assert _UV_RUN_PYRIGHT in entry, drift_msg
