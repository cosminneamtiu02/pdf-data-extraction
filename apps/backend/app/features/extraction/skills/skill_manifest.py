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
        # Empty-manifest lookup warning is once-per-instance: `lookup` is on
        # the request path, so a misconfigured deployment receiving traffic
        # would otherwise emit one warning per request. The manifest instance
        # carries a single-shot flag so operators get the "no skills ever
        # loaded" signal once per process (issue #386, PR #493 feedback).
        self._empty_warning_logged: bool = False

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
        `skill_lookup_on_empty_manifest` warning is emitted *once per
        manifest instance* before the raise so operators see the root-cause
        signal in-stream with the failing request instead of having to
        correlate with the `/ready` probe (issue #386). Subsequent lookups
        on the same empty instance stay silent on this channel — `lookup` is
        on the request path and we won't flood the log stream. The raised
        error type stays `SkillNotFoundError` for every call so the response
        payload is unchanged (PR #493 feedback).

        Exception chaining: a non-integer `version` still raises
        `SkillNotFoundError(...) from ValueError` regardless of whether the
        manifest is empty, so debuggability is identical in both modes.
        """
        if version == _LATEST:
            resolved = self._latest.get(name)
            if resolved is None:
                self._warn_if_empty(name, version)
                raise SkillNotFoundError(name=name, version=version)
            return self._skills[(name, resolved)]

        try:
            parsed = int(version)
        except ValueError as exc:
            self._warn_if_empty(name, version)
            raise SkillNotFoundError(name=name, version=version) from exc

        skill = self._skills.get((name, parsed))
        if skill is None:
            self._warn_if_empty(name, version)
            raise SkillNotFoundError(name=name, version=version)
        return skill

    def _warn_if_empty(self, name: str, version: str) -> None:
        """Emit the empty-manifest warning at most once per instance.

        Called from every `lookup` raise site so the warning fires whichever
        branch the miss took (``latest`` unresolved, integer-parse failure,
        or integer-not-present). The ``_empty_warning_logged`` flag is set
        after the first emission so subsequent calls stay silent — see the
        rationale in `lookup`.
        """
        if not self.is_empty or self._empty_warning_logged:
            return
        _logger.warning(
            "skill_lookup_on_empty_manifest",
            requested_name=name,
            requested_version=version,
        )
        self._empty_warning_logged = True
