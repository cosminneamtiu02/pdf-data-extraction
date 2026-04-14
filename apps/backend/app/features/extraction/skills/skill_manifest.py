"""SkillManifest — in-memory registry of validated skills.

Built once at startup from `SkillLoader.load` and frozen for the lifetime of
the process. `lookup` is a pure dict access; there is no disk I/O on the
request path. See PDFX-E002-F002 for the design rationale.
"""

from types import MappingProxyType
from typing import Final

from app.exceptions import SkillNotFoundError
from app.features.extraction.skills.skill import Skill

_LATEST: Final = "latest"


class SkillManifest:
    """Frozen registry keyed by `(name, version)` with `latest` resolution."""

    def __init__(self, loaded: dict[tuple[str, int], Skill]) -> None:
        latest: dict[str, int] = {}
        for name, version in loaded:
            current = latest.get(name)
            if current is None or version > current:
                latest[name] = version
        # MappingProxyType makes the internal dicts read-only at the type level,
        # reinforcing the "never mutated after construction" invariant.
        self._skills: MappingProxyType[tuple[str, int], Skill] = MappingProxyType(dict(loaded))
        self._latest: MappingProxyType[str, int] = MappingProxyType(latest)

    def lookup(self, name: str, version: str) -> Skill:
        """Return the `Skill` for `(name, version)` or raise `SkillNotFoundError`.

        `version` is either `"latest"` or a positive-integer string. Any other
        shape — a non-numeric string, an integer-like but missing version, or
        a name not present at all — raises `SkillNotFoundError` so callers
        never see partial results.
        """
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
