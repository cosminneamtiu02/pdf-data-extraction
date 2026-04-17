"""Regression guard for issue #139: the Linux `uv.lock` must not resolve to
CUDA-bundled torch wheels.

The service does no GPU work; Docling's ML pipeline runs on CPU. The
`[tool.uv.sources]` + `[[tool.uv.index]]` config in `apps/backend/pyproject.toml`
routes `torch`/`torchvision` through `https://download.pytorch.org/whl/cpu` on
Linux so the resulting Docker image doesn't pull the ~4 GB `nvidia-*` CUDA
runtime. A future dependency-metadata change could silently reintroduce them
(the earlier lock before this fix listed 18 CUDA/NVIDIA packages transitively).
This test pins the invariant: any `nvidia-*` or CUDA runtime leaking into the
lockfile trips the check at CI time, before the Docker image rebuilds and ships
a bloated artifact.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_LOCKFILE_PATH = Path(__file__).resolve().parents[3] / "uv.lock"

# Any package name prefix we forbid from the Linux-resolved lockfile. The list
# is the 18 CUDA/NVIDIA packages that vanished after the pytorch-cpu index
# override, plus `triton` (torch's CUDA-only JIT compiler).
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "nvidia-",
    "cuda-",
    "triton",
)


def _package_names_resolved_for_linux() -> list[str]:
    """Return every package name in `uv.lock` whose resolved wheel would
    apply on linux (either unconditionally or via a matching environment
    marker). The check is intentionally approximate: we inspect the
    package name alone because the forbidden set is itself Linux-specific
    (NVIDIA wheels only publish linux builds), so any entry appearing in
    `uv.lock` is by definition candidate for the Linux image.
    """
    data = tomllib.loads(_LOCKFILE_PATH.read_text(encoding="utf-8"))
    return [pkg["name"] for pkg in data.get("package", [])]


def test_uv_lock_has_no_cuda_or_nvidia_packages() -> None:
    names = _package_names_resolved_for_linux()
    offenders = sorted(
        name for name in names if any(name.startswith(prefix) for prefix in _FORBIDDEN_PREFIXES)
    )
    assert not offenders, (
        f"uv.lock re-introduced CUDA/NVIDIA packages (issue #139 regression): "
        f"{offenders}. Verify `[tool.uv.sources]` in `apps/backend/pyproject.toml` "
        f"still routes torch/torchvision through the `pytorch-cpu` index with "
        f"`marker = \"sys_platform == 'linux'\"` and that torch/torchvision "
        f"remain declared as direct dependencies (only direct deps are eligible "
        f"for uv source overrides)."
    )


def test_uv_lock_torch_linux_wheel_points_at_cpu_index() -> None:
    """Positive regression: at least one torch entry in `uv.lock` must be
    sourced from the `download.pytorch.org/whl/cpu` registry — this is
    the Linux-resolved record uv writes when `[tool.uv.sources]` routes
    torch through the CPU index. uv.lock is platform-agnostic metadata,
    so this assertion is valid on any host that runs the test
    (including the macOS dev machines that resolve the Linux entries
    via `sys_platform == 'linux'` markers).
    """
    data = tomllib.loads(_LOCKFILE_PATH.read_text(encoding="utf-8"))
    torch_pkgs = [pkg for pkg in data.get("package", []) if pkg["name"] == "torch"]
    assert torch_pkgs, "torch missing from uv.lock — is it still a direct dependency?"

    # uv writes the CPU-index registry into `[package.source]` for the
    # Linux-resolved torch entry (inline-table form: `source = { registry = "..." }`),
    # which tomllib parses as a dict. We treat that registry URL as the
    # authoritative marker — wheel URLs are published from a CDN mirror
    # (`download-r2.pytorch.org`) and are therefore unreliable for this check.
    cpu_registry_sources: list[str] = []
    for pkg in torch_pkgs:
        source = pkg.get("source", {})
        if not isinstance(source, dict):
            continue
        registry = source.get("registry", "") or source.get("url", "")
        if "download.pytorch.org/whl/cpu" in registry:
            cpu_registry_sources.append(registry)

    assert cpu_registry_sources, (
        "torch has no source entry pointing at `download.pytorch.org/whl/cpu` "
        "in uv.lock. The Linux marker on `[tool.uv.sources]` may be broken; verify "
        "`apps/backend/pyproject.toml` and re-run `uv lock`."
    )
