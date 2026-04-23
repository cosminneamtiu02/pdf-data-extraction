"""Internal helper for the architecture test suite (PDFX-E007-F004).

Centralizes subprocess invocation of `lint-imports`, repo-root path
computation, and shared `_inject` helper used by the meta-enforcement
and live-subprocess tests. Kept out of `conftest.py` so the names are
explicitly imported (and grep-able) rather than fixture-injected.

Performance note (issue #350): `copy_app_tree` maintains a module-level
session cache of the real `app/` tree. The first call does one real
`shutil.copytree`; subsequent calls build isolated per-test trees via
`os.link` hardlinks, which is a cheap inode-level syscall rather than a
byte-copy walk of ~113 `.py` files, with a portable byte-copy fallback for
filesystems that do not support hardlinks. The hardlink-support probe
itself is memoized by destination `st_dev` (Copilot review on PR #479):
the `cache.rglob("*")` walk and the `os.link` probe run at most once per
filesystem pair across the session. `inject_import_line` uses `os.replace`
to atomically swap the file contents, which both keeps the rewrite atomic
from concurrent readers and implicitly breaks any shared hardlink so
per-test injections never leak back into the shared cache.
"""

from __future__ import annotations

import atexit
import errno
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from tests import _paths

# errno values that indicate `os.link` itself is unsupported or refused,
# rather than a generic I/O fault. On those errnos we fall back to a
# normal byte-copy so the suite stays portable across filesystems.
# On others we re-raise so real setup bugs (notably `FileExistsError`
# when `dest` already exists) are surfaced instead of silently masked.
_HARDLINK_UNSUPPORTED_ERRNOS: Final[frozenset[int]] = frozenset(
    {
        errno.EXDEV,  # cross-device link
        errno.EPERM,  # operation not permitted (e.g. Windows share, sandboxed CI)
        errno.EOPNOTSUPP,  # operation not supported on fs
        errno.EINVAL,  # some fs report "invalid" instead of EOPNOTSUPP
    }
)

# Re-export the repo-root landmarks so existing importers (`from
# tests.unit.architecture._linter_subprocess import REPO_ROOT`) keep
# working without change. The single source of truth lives in
# `tests/_paths.py` (issue #402) so future layout changes touch one
# file, not eight.
REPO_ROOT: Final[Path] = _paths.REPO_ROOT
BACKEND_DIR: Final[Path] = _paths.BACKEND_DIR
REAL_APP_TREE: Final[Path] = _paths.APP_DIR
REAL_CONTRACTS_PATH: Final[Path] = BACKEND_DIR / "architecture" / "import-linter-contracts.ini"

# Session cache for `copy_app_tree`. Lazily populated on first call; reused
# for every subsequent call in the same pytest process. Intentionally
# module-level so it survives across parametrized cases without requiring
# test functions to depend on a session-scoped pytest fixture (the existing
# public API is a plain function call; keeping that shape avoids touching
# callers). See issue #350.
_app_tree_cache: Path | None = None

# Session cache for `_hardlinks_supported`. The hardlink-support result is a
# property of the (cache filesystem, destination filesystem) pair; as long as
# both sides stay on the same device we never need to re-probe. Keying by
# `st_dev` handles the theoretical case of `tmp_path` landing on a different
# filesystem than a prior call (e.g. a test that explicitly relocates its
# temp dir), at which point we re-probe and cache that result too. Without
# this cache, `copy_app_tree` would run `cache.rglob("*")` + an `os.link`
# probe on every invocation (~8x per unit run), partially offsetting the
# hardlink speedup the rest of the PR delivers. Copilot review on PR #479.
_hardlink_support_by_dev: dict[tuple[int, int], bool] = {}
# Memoized "one file to probe with" inside the cache tree. Computed once
# on the first `_hardlinks_supported` call after the cache is populated,
# reused forever after. The cache tree is immutable under the session
# contract — `copy_app_tree` builds *destinations* from it, never writes
# back — so a stale `_probe_src` is impossible unless the cache itself is
# cleared, and a cleared cache also clears this via `_reset_hardlink_cache`.
_probe_src: Path | None = None


def _reset_hardlink_cache() -> None:
    """Drop the memoized hardlink-support decision and probe-source file.

    Call this whenever `_app_tree_cache` is invalidated. Tests that reset the
    tree cache via `monkeypatch.setattr(..., '_app_tree_cache', None)` are
    expected to also reset the hardlink caches; the two companion tests in
    `test_copy_app_tree_cached.py` monkeypatch both names symmetrically.
    """
    global _probe_src  # noqa: PLW0603 — companion cache, see issue #350
    _hardlink_support_by_dev.clear()
    _probe_src = None


def resolve_lint_imports_binary() -> Path:
    """Return the path to the `lint-imports` console script in the active venv.

    `python -m importlinter` does not work because the package has no
    `__main__.py`. The CLI is only installed as a console script entry point
    in the venv's `bin/` directory, so the portable invocation is to find
    that script next to `sys.executable` and exec it directly.
    """
    bin_name = "lint-imports.exe" if os.name == "nt" else "lint-imports"
    candidate = Path(sys.executable).parent / bin_name
    if not candidate.exists():
        msg = (
            f"could not locate lint-imports binary next to sys.executable "
            f"({sys.executable}); expected {candidate}"
        )
        raise FileNotFoundError(msg)
    return candidate


def run_lint_imports(
    cwd: Path,
    contracts_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Invoke `lint-imports --config <contracts_path>` from `cwd`.

    `cwd` is the directory that holds the `app/` tree the linter should
    analyze. The function ensures `cwd` is on `PYTHONPATH` so that
    `root_package = app` from the contracts file resolves against the tree
    in `cwd`, not against any other `app` package that might already be
    importable.

    `os.environ.copy()` is used to inherit the parent process environment.
    The CLAUDE.md prohibition on `os.environ` targets reading config values
    in production code; building a subprocess environment dict here has no
    pydantic-settings equivalent and is the documented way to pass env vars
    to child processes.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{cwd}{os.pathsep}{env.get('PYTHONPATH', '')}"
    return subprocess.run(
        [str(resolve_lint_imports_binary()), "--config", str(contracts_path)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _ensure_app_tree_cache() -> Path:
    """Return the session-cached pristine copy of `REAL_APP_TREE`, creating it if needed.

    One real bytes-copy per pytest process. The cache lives under the system
    tempdir (not `tmp_path`, which is function-scoped and gets torn down
    between tests) and is cleaned up on interpreter exit via `atexit` so we
    do not leak `_contract_enforcement_app_cache_*` directories between
    pytest runs (issue #350: Copilot review on PR #479 flagged that pytest
    does not evict directories created via `tempfile.mkdtemp`).
    """
    global _app_tree_cache  # noqa: PLW0603 — module-level session cache, see issue #350
    if _app_tree_cache is not None and _app_tree_cache.exists():
        return _app_tree_cache

    # Rebuilding the tree cache invalidates the hardlink caches: the probe
    # file path (tied to the old cache tree) no longer exists, and even the
    # `st_dev` pair could shift if `tempfile.mkdtemp` lands on a different
    # filesystem than before. Reset both so the next `copy_app_tree` re-probes
    # cleanly rather than linking against a stale or vanished inode.
    _reset_hardlink_cache()
    cache_parent = Path(tempfile.mkdtemp(prefix="_contract_enforcement_app_cache_"))
    atexit.register(shutil.rmtree, cache_parent, ignore_errors=True)
    cache_dest = cache_parent / "app"
    shutil.copytree(
        REAL_APP_TREE,
        cache_dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _app_tree_cache = cache_dest
    return cache_dest


def _hardlinks_supported(cache: Path, probe_dir: Path) -> bool:
    """Probe whether we can `os.link` from `cache` into `probe_dir`.

    We test this up-front with one tiny sample file rather than letting
    `shutil.copytree(..., copy_function=os.link)` fail mid-walk, for two
    reasons:

    1. `shutil.copytree` wraps per-file copy_function errors in
       `shutil.Error` (not a subclass of `OSError`), so the error taxonomy
       after the fact is awkward to unpack.
    2. A probe keeps the fast-path `except`-free: real I/O failures
       (disk full, permission denied on `dest`, `FileExistsError` when the
       caller reused a non-empty dest) are never silently masked by a
       fallback rmtree + byte-copy retry. Copilot review on PR #479
       flagged the earlier catch-all as a footgun.

    `probe_dir` must be any existing directory on the same filesystem as
    the eventual copy destination. `copy_app_tree` passes `tmp_path`
    itself when that directory already exists, and falls back to
    `tmp_path.parent` otherwise (pytest's `tmp_path` is under the
    session-wide base dir, which always exists and shares the filesystem
    with the per-test subtree). The only requirement is that `probe_dir`
    be an existing directory sharing the destination filesystem.

    Returns True iff `os.link` succeeds on at least one file from `cache`
    into `probe_dir`. A False result funnels the caller into the byte-copy
    code path for the rest of the tree. Worst case (probe succeeds but a
    later `os.link` still fails) raises the original `shutil.Error`
    untouched — the suite aborts loudly, which is the intended behavior
    for a setup fault in CI.

    The decision is memoized per `(cache_dev, probe_dev)` pair: subsequent
    calls with the same filesystem pair skip both the `cache.rglob("*")`
    walk and the `os.link` probe entirely, returning the cached boolean.
    See the module-level `_hardlink_support_by_dev` / `_probe_src` docs
    and Copilot review on PR #479.
    """
    global _probe_src  # noqa: PLW0603 — companion cache, see issue #350
    # Fast path: this (cache_dev, probe_dev) pair has already been probed.
    # `st_dev` is the kernel's device id, stable across `os.stat` calls on
    # the same filesystem, so a cache keyed on it is correct even when
    # callers pass different concrete `probe_dir` paths.
    cache_dev = cache.stat().st_dev
    probe_dev = probe_dir.stat().st_dev
    cache_key = (cache_dev, probe_dev)
    cached = _hardlink_support_by_dev.get(cache_key)
    if cached is not None:
        return cached
    # Find one regular file in the cache. Any file works; we just need a
    # real inode to try linking. The cache always has at least one. The
    # chosen path is memoized so subsequent probes (different destination
    # filesystem, e.g. a test that relocates `tmp_path`) skip the rglob.
    if _probe_src is None or not _probe_src.is_file():
        for candidate in cache.rglob("*"):
            if candidate.is_file():
                _probe_src = candidate
                break
    if _probe_src is None:
        _hardlink_support_by_dev[cache_key] = False
        return False
    # Use an unambiguously unique filename so we never collide with an
    # existing file in `probe_dir`. `tempfile.mktemp` would race, but we
    # want a plain predictable path we can `unlink` afterward; the pid +
    # `id()` combo is sufficient because `probe_dir` is caller-scoped.
    probe_dst = probe_dir / f".hardlink-probe-{os.getpid()}-{id(cache)}"
    try:
        os.link(_probe_src, probe_dst)
    except OSError as exc:
        if exc.errno in _HARDLINK_UNSUPPORTED_ERRNOS:
            _hardlink_support_by_dev[cache_key] = False
            return False
        raise
    else:
        probe_dst.unlink()
        _hardlink_support_by_dev[cache_key] = True
        return True


def copy_app_tree(tmp_path: Path) -> Path:
    """Copy the real `apps/backend/app/` tree into `tmp_path/app` and return its root.

    Fast path via hardlinks from a session cache: the first call across the
    pytest session does a single real `shutil.copytree(REAL_APP_TREE, ...)`;
    subsequent calls do `shutil.copytree(<cache>, dest, copy_function=os.link)`,
    which creates new directory entries pointing at the cache's inodes rather
    than duplicating file bytes.

    Portability fallback: if the cache and `tmp_path` sit on different
    filesystems (EXDEV) or the filesystem does not support hardlinks
    (EPERM/EOPNOTSUPP/EINVAL on Windows shares, some sandboxed CI), a tiny
    up-front probe (`_hardlinks_supported`) returns False and we use a
    normal byte-copy so the suite stays portable. `inject_import_line`
    uses `os.replace` to rewrite files, which is hardlink-safe regardless
    of which code path produced `dest`. See issue #350 for the timing
    context (8 full copies per unit run pre-fix) and Copilot review on
    PR #479 for the fallback rationale.
    """
    cache = _ensure_app_tree_cache()
    dest = tmp_path / "app"
    # Probe in a parent that is guaranteed to exist: callers pass
    # `tmp_path` subdirs (e.g. `pytest.tmp_path / "first"`) that may not
    # have been created yet. The tree we probe with will live on the same
    # filesystem as `dest` because `tmp_path` inherits that property from
    # its parent.
    probe_dir = tmp_path if tmp_path.exists() else tmp_path.parent
    copy_function = os.link if _hardlinks_supported(cache, probe_dir) else shutil.copy2
    shutil.copytree(
        cache,
        dest,
        copy_function=copy_function,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def copy_contracts(tmp_path: Path) -> Path:
    """Copy the real contracts INI into `tmp_path` and return the destination path."""
    dest = tmp_path / "import-linter-contracts.ini"
    dest.write_bytes(REAL_CONTRACTS_PATH.read_bytes())
    return dest


def inject_import_line(target: Path, line: str) -> None:
    """Prepend `line` to `target` while preserving any leading `from __future__` imports.

    Python requires `from __future__ import ...` statements to be the very
    first statement in a module. Naive prepending would put the injected
    line above a `__future__` import and trigger a SyntaxError, which the
    static AST parser used by import-linter (via `grimp`) would either skip
    or error on - either way invalidating the meta-enforcement test.

    This function:
      1. Walks the file's leading lines, preserving any `from __future__`
         imports as a head block.
      2. Inserts the injected line directly after the head block.
      3. Writes the rebuilt content to a sibling temp file, then uses
         `os.replace` to atomically move it into place. `os.replace` on POSIX
         unlinks the old directory entry and installs the new inode in a
         single syscall, so (a) the path never disappears from the reader's
         point of view and (b) any hardlink from the session cache
         maintained by `copy_app_tree` (issue #350) is broken automatically:
         the cache keeps its original inode while our path now points at a
         fresh one. This replaces an earlier `unlink() + write_text()`
         sequence flagged by Copilot on PR #479 as non-atomic.
    """
    if not target.exists():
        msg = f"scratch-tree target does not exist: {target}"
        raise FileNotFoundError(msg)

    # Use explicit UTF-8 on both read and write so the helper is locale-
    # independent. The `app/` tree is pure Python source, which PEP 3120
    # pins to UTF-8, so round-tripping through the default locale encoding
    # on non-UTF-8 systems (Windows cp1252, some CJK locales) would silently
    # corrupt non-ASCII module docstrings. Copilot review on PR #479.
    original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    head: list[str] = []
    body_start = 0
    for idx, raw_line in enumerate(original_lines):
        stripped = raw_line.strip()
        if stripped.startswith("from __future__"):
            head.append(raw_line)
            body_start = idx + 1
        elif stripped == "" and head:
            # Trailing blank line after a future import block - keep it with the head.
            head.append(raw_line)
            body_start = idx + 1
        else:
            break

    rebuilt = "".join(head) + line + "\n" + "".join(original_lines[body_start:])
    # Write rebuilt content to a sibling temp file in the same directory so
    # `Path.replace` (which delegates to `os.replace`) is an intra-filesystem
    # atomic rename. Using the same parent dir is required because cross-
    # filesystem `os.replace`/rename typically fails (for example with EXDEV)
    # rather than falling back to a non-atomic copy+unlink.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.inject-",
        dir=str(target.parent),
    )
    # Ownership of `tmp_fd` is transferred to `os.fdopen` iff that call
    # succeeds. On the (extremely rare) path where `os.fdopen` itself
    # raises before wrapping, the raw fd would leak. Guard that with a
    # nested try/except that closes `tmp_fd` on the pre-wrap failure path
    # only, then re-raises so the outer cleanup removes the temp file.
    # Copilot review on PR #479 flagged the fd-leak window.
    try:
        try:
            handle = os.fdopen(tmp_fd, "w", encoding="utf-8")
        except BaseException:
            os.close(tmp_fd)
            raise
        with handle:
            handle.write(rebuilt)
        Path(tmp_name).replace(target)
    except BaseException:
        # On any failure, clean up the temp file rather than leaking it into
        # the scratch tree (which would confuse `lint-imports`' module walk).
        Path(tmp_name).unlink(missing_ok=True)
        raise
