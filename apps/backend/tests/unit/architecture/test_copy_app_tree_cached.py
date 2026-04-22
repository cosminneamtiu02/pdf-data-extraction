"""Regression: `copy_app_tree` does one bytes-copy of `app/` per session.

Issue #350: every parametrized case in `test_contract_enforcement.py` was
doing a full `shutil.copytree(REAL_APP_TREE, dest)` — 4 third-party cases +
3 layer-DAG cases + 1 clean-slate = 8 full tree copies on every `task test:unit`
run, eating 15-25 s of the <10 s unit-test budget.

The fix is a module-level session cache: the first call does one real copy
into a cache dir; subsequent calls produce isolated trees via `os.link`
(hardlinks) from the cache, which is a cheap syscall rather than a byte-copy
walk. This suite pins that invariant so a future refactor cannot regress it.

Each test here intentionally resets `_app_tree_cache` to `None` (via
`monkeypatch.setattr`, which pytest auto-undoes on teardown so the session
cache for sibling tests is unaffected) so it can observe cold-start behavior
from a known-clean slate — i.e. assert on the initial cache-populating copy
and the hardlink-driven second call, not on whatever state a prior test
happened to leave behind.

`test_inject_import_line_does_not_mutate_the_shared_cache` is the companion
invariant: the cache must not be mutated when a per-test tree's file is
rewritten by `inject_import_line`, or tests would cross-pollute and U9
(clean slate) could pass for the wrong reason.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from . import _linter_subprocess
from ._linter_subprocess import (
    REAL_APP_TREE,
    copy_app_tree,
    inject_import_line,
)


def test_copy_app_tree_copies_real_source_at_most_once_across_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two `copy_app_tree` calls must trigger at most one real bytes-copy of `app/`.

    Counts any `shutil.copytree` invocation whose `src` resolves to the real
    app tree. Hardlink-backed per-test copies are allowed (they call
    `shutil.copytree` with `copy_function=os.link` and `src=<cache>`, not
    `src=REAL_APP_TREE`) and do not increment this counter.
    """
    # The module-level cache may have been primed by an earlier test in this
    # session; reset it so we observe the cold-start copy from a clean slate.
    monkeypatch.setattr(_linter_subprocess, "_app_tree_cache", None, raising=False)
    # Reset the companion hardlink caches so the probe runs cold too; otherwise
    # a prior test in the session could leave a `_probe_src` pointing into a
    # now-vanished cache tree. Cleared attributes are auto-restored on teardown.
    monkeypatch.setattr(_linter_subprocess, "_probe_src", None, raising=False)
    monkeypatch.setattr(
        _linter_subprocess,
        "_hardlink_support_by_dev",
        {},
        raising=False,
    )

    real_copytree = shutil.copytree
    real_app_tree_resolved = REAL_APP_TREE.resolve()
    real_source_copy_count = 0

    def counting_copytree(src: object, dst: object, *args: object, **kwargs: object) -> object:
        nonlocal real_source_copy_count
        if Path(str(src)).resolve() == real_app_tree_resolved:
            real_source_copy_count += 1
        return real_copytree(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "copytree", counting_copytree)

    first_dest = copy_app_tree(tmp_path / "first")
    second_dest = copy_app_tree(tmp_path / "second")

    assert first_dest.exists(), "first copy did not materialize an `app/` directory"
    assert second_dest.exists(), "second copy did not materialize an `app/` directory"
    assert first_dest != second_dest, "per-call destinations must be isolated"
    assert real_source_copy_count <= 1, (
        f"`copy_app_tree` did {real_source_copy_count} full bytes-copies of "
        f"{REAL_APP_TREE}; the session cache should limit this to at most 1. "
        "See issue #350."
    )


def test_copy_app_tree_probes_hardlink_support_at_most_once_per_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hardlink-support probe must be memoized: at most one `os.link` probe per fs pair.

    Copilot review on PR #479 flagged that `copy_app_tree` called
    `_hardlinks_supported` on every invocation, which does a `cache.rglob("*")`
    walk + an `os.link` probe each time. The fix memoizes the result by
    destination filesystem (`st_dev`) so subsequent calls in the same
    session take the fast path. This test pins that invariant by counting
    `os.link` calls that originate from the probe (identified by the
    `.hardlink-probe-` filename prefix) across two `copy_app_tree` calls
    landing on the same filesystem (pytest `tmp_path` always does).
    """
    monkeypatch.setattr(_linter_subprocess, "_app_tree_cache", None, raising=False)
    monkeypatch.setattr(_linter_subprocess, "_probe_src", None, raising=False)
    monkeypatch.setattr(
        _linter_subprocess,
        "_hardlink_support_by_dev",
        {},
        raising=False,
    )

    real_link = _linter_subprocess.os.link
    probe_link_count = 0

    def counting_link(src: object, dst: object, *args: object, **kwargs: object) -> object:
        nonlocal probe_link_count
        if ".hardlink-probe-" in Path(str(dst)).name:
            probe_link_count += 1
        return real_link(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_linter_subprocess.os, "link", counting_link)

    copy_app_tree(tmp_path / "first")
    copy_app_tree(tmp_path / "second")

    assert probe_link_count <= 1, (
        f"`_hardlinks_supported` probed `os.link` {probe_link_count} times "
        "across two `copy_app_tree` calls on the same filesystem; the "
        "memoization should keep this at 1. See Copilot review on PR #479."
    )


def test_inject_import_line_does_not_mutate_the_shared_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutating a per-test tree must not leak back into the session cache.

    If `copy_app_tree` uses hardlinks for speed, `inject_import_line` must
    break the link (copy-on-write) before rewriting the file, or every
    subsequent test would see the injected line in its "fresh" tree and
    U7/U8/U9 would all read a polluted baseline.
    """
    monkeypatch.setattr(_linter_subprocess, "_app_tree_cache", None, raising=False)
    # Reset the companion hardlink caches so the probe runs cold too; otherwise
    # a prior test in the session could leave a `_probe_src` pointing into a
    # now-vanished cache tree. Cleared attributes are auto-restored on teardown.
    monkeypatch.setattr(_linter_subprocess, "_probe_src", None, raising=False)
    monkeypatch.setattr(
        _linter_subprocess,
        "_hardlink_support_by_dev",
        {},
        raising=False,
    )

    victim_rel = "features/extraction/intelligence/intelligence_provider.py"
    first_dest = copy_app_tree(tmp_path / "first")
    second_dest = copy_app_tree(tmp_path / "second")

    first_target = first_dest / victim_rel
    second_target = second_dest / victim_rel
    second_original_bytes = second_target.read_bytes()

    canary = "import canary_module_issue_350"
    inject_import_line(first_target, canary)

    assert canary in first_target.read_text(encoding="utf-8"), (
        "injection did not land in the first tree"
    )
    assert second_target.read_bytes() == second_original_bytes, (
        "injection leaked across per-test trees — the session cache is being "
        "mutated in place instead of copy-on-write from hardlinks. See issue #350."
    )
