"""BenchmarkSettings — pydantic-settings model for ``scripts/benchmark`` (issues #237, #272).

The benchmark script is a CLI operator tool that reads its own ``BENCH_*``
environment variables. CLAUDE.md forbids ``os.environ`` / ``os.getenv``
categorically, so those env vars flow through a dedicated
``BaseSettings`` subclass with ``env_prefix='BENCH_'`` instead. Keeping
the benchmark knobs off the main :class:`app.core.config.Settings`
preserves the "application config vs operator-script config" separation
while still satisfying the pydantic-settings-everywhere invariant.

This module lives next to its sole consumer (``scripts/benchmark.py``)
rather than under ``app/core/`` because ``app/core/`` is scoped to
runtime service config and logging (CLAUDE.md). The leading underscore
marks it as module-private to the ``scripts`` package — no code outside
``scripts/`` should import it (issue #272).

CLI flags on the script still win over env vars — ``parse_args`` uses
the ``BenchmarkSettings`` defaults as the argparse ``default=`` values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_string_to_none(v: object) -> object:
    """Coerce an empty / whitespace-only ``BENCH_SERVICE_PID`` env value to ``None``.

    The Taskfile passes ``BENCH_SERVICE_PID=""`` when the operator does
    not supply a PID, and pydantic-settings would otherwise fail to
    coerce ``""`` into ``int | None``. Collapse the blank to ``None``
    before the int parser sees it.
    """
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


class BenchmarkSettings(BaseSettings):
    """Operator-facing configuration for ``scripts/benchmark``.

    Fields mirror the CLI flags on the benchmark command and carry the
    same defaults the script used to hardcode. Each field corresponds
    to exactly one ``BENCH_<NAME>`` environment variable thanks to
    ``env_prefix='BENCH_'``.
    """

    model_config = SettingsConfigDict(
        env_prefix="BENCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = "http://localhost:8000"
    iterations: Annotated[int, Field(gt=0)] = 10
    fixtures_dir: Path = Path("fixtures/bench")
    skill_name: str = "invoice"
    skill_version: str = "1"
    service_pid: Annotated[int | None, BeforeValidator(_empty_string_to_none)] = None
    warmup: Annotated[int, Field(ge=0)] = 1
    timeout: Annotated[float, Field(gt=0)] = 300.0
