"""SkillManifest — in-memory registry of validated skills.

Built once at startup from `SkillLoader.load` and frozen for the lifetime of
the process. `lookup` is a pure dict access; there is no disk I/O on the
request path. See PDFX-E002-F002 for the design rationale.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import structlog

from app.exceptions import SkillNotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.features.extraction.skills.skill import Skill

_LATEST: Final = "latest"

_logger = structlog.get_logger(__name__)


class SkillManifest:
    """Frozen registry keyed by `(name, version)` with `latest` resolution."""

    def __init__(self, loaded: dict[tuple[str, int], Skill]) -> None:
        latest: dict[str, int] = {}
        for name, version in loaded:
            current = latest.get(name)
            if current is None or version > current:
                latest[name] = version
        # `MappingProxyType` gives a read-only view; annotating with `Mapping`
        # avoids any subscripting quirks at runtime and keeps consumers honest.
        self._skills: Mapping[tuple[str, int], Skill] = MappingProxyType(dict(loaded))
        self._latest: Mapping[str, int] = MappingProxyType(latest)

    @property
    def is_empty(self) -> bool:
        """Return True when no skills were registered at load time.

        Consumed by the `/ready` probe (``health_router.ready``) so the
        container can report unhealthy when operators ship a bare image
        (`apps/backend/skills/` holding only `.gitkeep`) without mounting
        a real skills directory, which would otherwise surface as every
        extraction request 404-ing on lookup.
        """
        return len(self._skills) == 0

    def lookup(self, name: str, version: str) -> Skill:
        """Return the `Skill` for `(name, version)` or raise `SkillNotFoundError`.

        `version` is either `"latest"` or a positive-integer string. Any other
        shape — a non-numeric string, an integer-like but missing version, or
        a name not present at all — raises `SkillNotFoundError` so callers
        never see partial results.

        If the manifest is empty (no skills ever loaded), a
        `skill_lookup_on_empty_manifest` warning is emitted before the raise
        so operators see the root-cause signal in-stream with the failing
        request instead of having to correlate with the `/ready` probe
        (issue #386). The raised error type stays `SkillNotFoundError` so the
        response payload is unchanged.
        """
        if not self._skills:
            _logger.warning(
                "skill_lookup_on_empty_manifest",
                requested_name=name,
                requested_version=version,
            )
            raise SkillNotFoundError(name=name, version=version)

        if version == _LATEST:
            resolved = self._latest.get(name)
            if resolved is None:
                raise SkillNotFoundError(name=name, version=version)
            return self._skills[(name, resolved)]

        try:
            parsed = int(version)
        except ValueError as exc:
            raise SkillNotFoundError(name=name, version=version) from exc

        skill = self._skills.get((name, parsed))
        if skill is None:
            raise SkillNotFoundError(name=name, version=version)
        return skill
