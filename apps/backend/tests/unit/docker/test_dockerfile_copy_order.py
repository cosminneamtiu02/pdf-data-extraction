"""Static assertion on builder-stage COPY/RUN ordering in `backend.Dockerfile`.

Issue #361 flagged that the builder stage of `infra/docker/backend.Dockerfile`
was arranged in an order that defeated BuildKit's layer cache on the
expensive dependency-install step. A source-only edit under
`apps/backend/app/` or `apps/backend/skills/` should NOT invalidate the
`uv sync --no-install-project` layer — only the final project-install
RUN should re-run.

The correct order inside the `builder` stage is:

    1. COPY the dependency manifests (`pyproject.toml`, `uv.lock`) from
       the build context.
    2. RUN `uv sync --no-install-project` to install every pinned
       third-party wheel into `/app/.venv`. This layer only invalidates
       when the manifests themselves change.
    3. COPY the application source (`apps/backend/app`,
       `apps/backend/skills`) from the build context.
    4. RUN `uv sync` (without `--no-install-project`) so the project's own
       code is wired into the venv. Only this final RUN re-executes on a
       source-only edit, and it is the cheap step — all third-party
       resolution is already cached from the earlier RUN.

This test parses the Dockerfile, focuses on the `builder` stage, and
asserts that the above order holds. It is deliberately strict: any future
PR that shuffles the order (e.g. by sneaking a source COPY above the
dep-install RUN "just for a one-off fix") must update this test too, at
which point the author will have to re-justify the regression.

Related commits:

- `7fac539 fix(docker): remove duplicate pyproject.toml COPY in builder
  stage (closes #292)` — removed the earlier line-32 `COPY pyproject.toml`
  that partially masked the cache-ordering fix.
- Issue #361 — the remaining ordering guard pinned by this file.

The parser intentionally only inspects the `builder` stage: the runtime
stage's `COPY --from=builder` lines copy already-built artifacts between
stages and do not interact with the build-context layer cache that this
test exists to protect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

# parents[5] walks: this file -> docker/ -> unit/ -> tests/ -> backend/ ->
# apps/ -> repo root. Same convention as
# `tests/unit/docker/test_dockerignore_at_repo_root.py`.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_DOCKERFILE_PATH: Final[Path] = _REPO_ROOT / "infra" / "docker" / "backend.Dockerfile"

# Path fragments (relative to the build context, which is the repo root) that
# identify each category of COPY. A line is classified by substring match on
# the normalized instruction body.
_MANIFEST_COPY_PATHS: Final[tuple[str, ...]] = (
    "apps/backend/pyproject.toml",
    "apps/backend/uv.lock",
)
_SOURCE_COPY_PATHS: Final[tuple[str, ...]] = (
    "apps/backend/app",
    "apps/backend/skills",
)

# The dep-install RUN is the one that uses `--no-install-project` — that is
# the flag that defines "install dependencies, skip the project itself".
# The project-install RUN is the one that omits that flag.
_DEP_INSTALL_MARKER: Final[str] = "--no-install-project"
_UV_SYNC_MARKER: Final[str] = "uv sync"


def _logical_lines(dockerfile_text: str) -> list[str]:
    """Collapse backslash continuations and strip comment lines.

    The Dockerfile spec lets an instruction span multiple physical lines
    using a trailing backslash. For ordering analysis we care about
    logical instructions, so fold those together first, then drop comments
    and blank lines. Comments inside a continued instruction are not
    legal in Dockerfile syntax and are not handled specially here.
    """
    folded: list[str] = []
    buffer: list[str] = []
    for raw_line in dockerfile_text.splitlines():
        stripped = raw_line.rstrip()
        if stripped.endswith("\\"):
            buffer.append(stripped[:-1].rstrip())
            continue
        buffer.append(stripped)
        folded.append(" ".join(part for part in buffer if part))
        buffer = []
    if buffer:
        folded.append(" ".join(part for part in buffer if part))
    return [line for line in folded if line and not line.lstrip().startswith("#")]


def _builder_stage_instructions(dockerfile_text: str) -> list[str]:
    """Return the logical instructions scoped to the `builder` stage.

    A multi-stage Dockerfile delimits stages with `FROM ... AS <name>`
    lines. The cache-ordering invariant this test enforces applies to
    the first stage (named `builder`) only: the runtime stage uses
    `COPY --from=builder ...` lines that transfer already-built
    artifacts and do not participate in the build-context cache.
    """
    instructions = _logical_lines(dockerfile_text)
    start: int | None = None
    end: int | None = None
    for index, line in enumerate(instructions):
        tokens = line.split()
        if not tokens or tokens[0].upper() != "FROM":
            continue
        # `FROM image AS name` — `AS` and name are tokens[-2] and tokens[-1].
        is_named_builder = (
            len(tokens) >= 4 and tokens[-2].upper() == "AS" and tokens[-1] == "builder"
        )
        if start is None and is_named_builder:
            start = index
            continue
        if start is not None and end is None:
            end = index
            break
    if start is None:
        pytest.fail(
            f"{_DOCKERFILE_PATH}: could not find a `FROM <image> AS builder` "
            "line. Issue #361's cache-ordering invariant is scoped to the "
            "builder stage; if the multi-stage naming has changed, update "
            "this test."
        )
    if end is None:
        end = len(instructions)
    return instructions[start:end]


def _classify_copy(instruction: str) -> str | None:
    """Return `manifest`, `source`, `cross-stage`, or None for non-COPY lines.

    `COPY --from=...` lines copy between build stages or from a named
    external image (e.g. the uv binary from `ghcr.io/astral-sh/uv`) and
    do not pull from the build context, so they do not participate in
    the source-change cache-invalidation story. They are classified as
    `cross-stage` and excluded from the ordering assertions.
    """
    tokens = instruction.split()
    if not tokens or tokens[0].upper() != "COPY":
        return None
    if any(token.startswith("--from=") for token in tokens[1:]):
        return "cross-stage"
    body = instruction
    if any(path in body for path in _MANIFEST_COPY_PATHS):
        return "manifest"
    if any(path in body for path in _SOURCE_COPY_PATHS):
        return "source"
    return None


def _copy_contains_path(instruction: str, path: str) -> bool:
    """Return True if `instruction` is a build-context COPY that carries `path`.

    Same classification rules as `_classify_copy`: only COPYs without a
    `--from=...` flag count (cross-stage COPYs do not pull from the build
    context and are excluded from the manifest/source ordering story). The
    check is intentionally substring-based so a single `COPY a b ./` line
    that bundles multiple manifests registers a hit for each path it
    carries.
    """
    tokens = instruction.split()
    if not tokens or tokens[0].upper() != "COPY":
        return False
    if any(token.startswith("--from=") for token in tokens[1:]):
        return False
    return path in instruction


def _is_dep_install_run(instruction: str) -> bool:
    tokens = instruction.split()
    if not tokens or tokens[0].upper() != "RUN":
        return False
    return _UV_SYNC_MARKER in instruction and _DEP_INSTALL_MARKER in instruction


def _is_project_install_run(instruction: str) -> bool:
    tokens = instruction.split()
    if not tokens or tokens[0].upper() != "RUN":
        return False
    return _UV_SYNC_MARKER in instruction and _DEP_INSTALL_MARKER not in instruction


def _first_index(predicate_results: list[bool]) -> int | None:
    for index, value in enumerate(predicate_results):
        if value:
            return index
    return None


def _last_index(predicate_results: list[bool]) -> int | None:
    last: int | None = None
    for index, value in enumerate(predicate_results):
        if value:
            last = index
    return last


def test_dockerfile_exists() -> None:
    """Sanity check: the Dockerfile path this test pins must exist.

    If this fails, the Dockerfile has been moved/renamed and every other
    test in this module will emit confusing file-missing errors. Surface
    the root cause first so the fix is obvious.
    """
    assert _DOCKERFILE_PATH.is_file(), (
        f"expected backend Dockerfile at {_DOCKERFILE_PATH}. If the "
        "Dockerfile location has changed, update `_DOCKERFILE_PATH` in "
        f"{__file__} and every other test under `tests/unit/docker/`."
    )


def test_builder_stage_copies_manifests_before_dep_install() -> None:
    """Both manifests must be COPYd, and the dep-install RUN must follow them.

    If a manifest COPY happens AFTER the dep-install RUN, the RUN has
    nothing to install against on first build and re-runs fully on every
    manifest edit on subsequent builds. Either failure mode defeats the
    caching intent of the split dep/project installs.

    The earlier version of this guard only asserted that *at least one*
    manifest COPY preceded the dep-install RUN, which would pass if, e.g.,
    `pyproject.toml` was copied but `uv.lock` was dropped (so the install
    ran unpinned). The loop below requires every path in
    `_MANIFEST_COPY_PATHS` to appear at least once before the dep-install
    RUN so both manifests are individually pinned.
    """
    instructions = _builder_stage_instructions(_DOCKERFILE_PATH.read_text(encoding="utf-8"))
    manifest_indexes_by_path: dict[str, list[int]] = {
        path: [index for index, line in enumerate(instructions) if _copy_contains_path(line, path)]
        for path in _MANIFEST_COPY_PATHS
    }
    is_manifest = [_classify_copy(line) == "manifest" for line in instructions]
    is_dep_install = [_is_dep_install_run(line) for line in instructions]
    last_manifest = _last_index(is_manifest)
    first_dep_install = _first_index(is_dep_install)
    missing_manifests = [path for path, indexes in manifest_indexes_by_path.items() if not indexes]
    assert not missing_manifests, (
        f"{_DOCKERFILE_PATH}: the builder stage is missing required manifest "
        f"COPY(s) for {missing_manifests}. The layer-cache ordering fix for "
        "issue #361 requires BOTH `apps/backend/pyproject.toml` AND "
        "`apps/backend/uv.lock` to be present in the image before the "
        "dep-install RUN, otherwise `uv sync --frozen` has nothing to pin "
        "against."
    )
    assert last_manifest is not None, (
        f"{_DOCKERFILE_PATH}: no COPY of {list(_MANIFEST_COPY_PATHS)} found in "
        "the builder stage. The layer-cache ordering fix for issue #361 requires "
        "at least one manifest COPY before the dep-install RUN."
    )
    assert first_dep_install is not None, (
        f"{_DOCKERFILE_PATH}: no `RUN uv sync ... {_DEP_INSTALL_MARKER}` "
        "line found in the builder stage. Issue #361's cache-ordering fix "
        "depends on the dep-install RUN being split from the project-install "
        "RUN; if the install strategy has changed, update this test."
    )
    assert last_manifest < first_dep_install, (
        f"{_DOCKERFILE_PATH}: manifest COPY at instruction index {last_manifest} "
        f"runs AFTER dep-install RUN at index {first_dep_install}. The "
        "dep-install layer would have no manifests to install against. "
        "Reorder so `COPY apps/backend/pyproject.toml apps/backend/uv.lock ./` "
        "precedes `RUN uv sync --frozen --no-dev --no-install-project`. "
        "See issue #361."
    )
    # Each manifest path must be COPYd at least once before the dep-install RUN.
    late_manifests = {
        path: indexes
        for path, indexes in manifest_indexes_by_path.items()
        if all(index > first_dep_install for index in indexes)
    }
    assert not late_manifests, (
        f"{_DOCKERFILE_PATH}: manifest COPY(s) for {list(late_manifests)} "
        f"appear only AFTER the dep-install RUN at index {first_dep_install} "
        f"(found at indexes {late_manifests}). "
        "Issue #361's fix requires every manifest to be present BEFORE the "
        "dep-install RUN."
    )


def test_builder_stage_dep_install_runs_before_source_copy() -> None:
    """Source COPYs must follow the dep-install RUN.

    This is the core layer-cache invariant of issue #361. If any source
    directory (`apps/backend/app`, `apps/backend/skills`) is COPYd
    before the dep-install RUN, then a single source edit invalidates
    the dep-install layer and every subsequent build re-downloads and
    re-resolves every pinned wheel. On a torch-CPU-heavy image that is
    minutes of wall-clock time per edit.
    """
    instructions = _builder_stage_instructions(_DOCKERFILE_PATH.read_text(encoding="utf-8"))
    is_source = [_classify_copy(line) == "source" for line in instructions]
    is_dep_install = [_is_dep_install_run(line) for line in instructions]
    first_dep_install = _first_index(is_dep_install)
    first_source = _first_index(is_source)
    assert first_source is not None, (
        f"{_DOCKERFILE_PATH}: no COPY of {list(_SOURCE_COPY_PATHS)} found in "
        "the builder stage. The Dockerfile must ship the application source; "
        "if the COPY paths have changed, update `_SOURCE_COPY_PATHS` in "
        f"{__file__}."
    )
    assert first_dep_install is not None, (
        f"{_DOCKERFILE_PATH}: no `RUN uv sync ... {_DEP_INSTALL_MARKER}` "
        "line found in the builder stage."
    )
    assert first_dep_install < first_source, (
        f"{_DOCKERFILE_PATH}: source COPY at instruction index {first_source} "
        f"runs BEFORE dep-install RUN at index {first_dep_install}. A source "
        "edit would invalidate the dep-install layer and force full wheel "
        "resolution on every rebuild. Reorder so `RUN uv sync ... "
        f"{_DEP_INSTALL_MARKER}` precedes `COPY apps/backend/app ./app` and "
        "`COPY apps/backend/skills ./skills`. See issue #361."
    )


def test_builder_stage_source_copy_precedes_project_install() -> None:
    """Project-install RUN must follow every source COPY.

    If `RUN uv sync --frozen --no-dev` (project install) runs before the
    source COPYs, the venv won't have the project's own editable-install
    metadata wired up against the actual source tree, and imports of
    `app.*` in subsequent layers will fail at runtime. The split is
    load-bearing for both caching AND correctness.

    The guard uses the FIRST project-install index rather than the last
    one, because "no project-install may run before source is copied"
    means the very first such RUN must already be preceded by every
    source COPY. Using the last index would silently accept a stray
    project-install above the source COPYs as long as a second one
    appeared below — exactly the regression this test exists to catch.
    """
    instructions = _builder_stage_instructions(_DOCKERFILE_PATH.read_text(encoding="utf-8"))
    is_source = [_classify_copy(line) == "source" for line in instructions]
    is_project_install = [_is_project_install_run(line) for line in instructions]
    last_source = _last_index(is_source)
    first_project_install = _first_index(is_project_install)
    assert last_source is not None, (
        f"{_DOCKERFILE_PATH}: no COPY of {list(_SOURCE_COPY_PATHS)} found in the builder stage."
    )
    assert first_project_install is not None, (
        f"{_DOCKERFILE_PATH}: no project-install `RUN uv sync ...` (the one "
        f"WITHOUT `{_DEP_INSTALL_MARKER}`) found in the builder stage. Issue "
        "#361's cache-ordering fix requires splitting the install into two "
        "RUNs; if that has changed, update this test."
    )
    assert last_source < first_project_install, (
        f"{_DOCKERFILE_PATH}: project-install RUN at instruction index "
        f"{first_project_install} runs BEFORE source COPY at index "
        f"{last_source}. The project install would have no source to wire "
        "in. Reorder so every `COPY apps/backend/app ./app` / "
        "`COPY apps/backend/skills ./skills` precedes `RUN uv sync --frozen "
        "--no-dev`. See issue #361."
    )


def test_builder_stage_has_single_manifest_copy_block() -> None:
    """The manifest files must be COPYd in a single contiguous block.

    Historically (before PR #298 / issue #292) the builder stage
    re-copied `pyproject.toml` a second time just above the project-
    install RUN. That second COPY was a no-op (the file was already in
    the image from the earlier COPY) but it re-introduced source-like
    behaviour for `pyproject.toml`: any edit to the manifest would
    invalidate TWO layers instead of one, and future contributors
    interpreting the Dockerfile would not know which COPY was the
    "real" one.

    Guards (both must hold):
      1. Every manifest COPY index must be consecutive in the builder
         stage's instruction list (the "single contiguous block"
         invariant the test name promises). A non-COPY or non-manifest
         instruction (e.g. a RUN, or a source COPY) between two
         manifest COPYs splits the block and is rejected.
      2. Every manifest COPY must appear above the dep-install RUN.
         Any manifest COPY AFTER that RUN is the #292 regression
         returning even if it happens to sit next to another manifest
         COPY.
    """
    instructions = _builder_stage_instructions(_DOCKERFILE_PATH.read_text(encoding="utf-8"))
    is_manifest = [_classify_copy(line) == "manifest" for line in instructions]
    is_dep_install = [_is_dep_install_run(line) for line in instructions]
    first_dep_install = _first_index(is_dep_install)
    assert first_dep_install is not None, (
        f"{_DOCKERFILE_PATH}: no `RUN uv sync ... {_DEP_INSTALL_MARKER}` "
        "line found in the builder stage."
    )
    manifest_indexes = [index for index, flag in enumerate(is_manifest) if flag]
    assert manifest_indexes, (
        f"{_DOCKERFILE_PATH}: no manifest COPY found in the builder stage. "
        "The contiguity guard presupposes at least one such COPY; if the "
        "Dockerfile no longer copies dependency manifests, the dep-install "
        "RUN would have nothing to install against — see issue #361."
    )
    gaps = [
        (manifest_indexes[pos - 1], manifest_indexes[pos])
        for pos in range(1, len(manifest_indexes))
        if manifest_indexes[pos] != manifest_indexes[pos - 1] + 1
    ]
    assert not gaps, (
        f"{_DOCKERFILE_PATH}: manifest COPY lines at instruction indexes "
        f"{manifest_indexes} are not contiguous — non-manifest instructions "
        f"appear between them at gaps {gaps}. The issue #292 regression "
        "pattern was a second `COPY apps/backend/pyproject.toml` later in "
        "the builder stage; consolidate manifest COPYs into a single "
        "adjacent block so only one image layer depends on manifest "
        "contents."
    )
    stray_manifest_indexes = [index for index in manifest_indexes if index > first_dep_install]
    assert not stray_manifest_indexes, (
        f"{_DOCKERFILE_PATH}: found manifest COPY lines AFTER the dep-install "
        f"RUN at instruction indexes {stray_manifest_indexes}. This is the "
        "issue #292 regression: a duplicate `COPY apps/backend/pyproject.toml` "
        "after the project source COPYs turns the manifest into a "
        "source-like file for caching purposes. Delete the redundant COPY "
        "and rely on the earlier one before the dep-install RUN."
    )
