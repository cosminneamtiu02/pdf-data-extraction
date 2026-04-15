"""SkillLoader — startup-time filesystem walker and validator.

Scans `skills_dir` with a strict two-level layout
(`<skills_dir>/<name>/<version>.yaml`), validates every matched file via
`SkillYamlSchema.load_from_file`, converts to `Skill` with merged Docling
defaults, and returns a `dict[(name, version), Skill]`. All problems are
aggregated into one `SkillValidationFailedError` so the operator sees every
offender in a single fail-fast boot.
"""

from pathlib import Path

import structlog

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills.skill import Skill
from app.features.extraction.skills.skill_docling_config import SkillDoclingConfig
from app.features.extraction.skills.skill_yaml_schema import SkillYamlSchema

_logger = structlog.get_logger(__name__)


class SkillLoader:
    """Walk `skills_dir` and produce a `(name, version) -> Skill` dict."""

    def __init__(self, default_docling: SkillDoclingConfig | None = None) -> None:
        self._default_docling = default_docling or SkillDoclingConfig()

    def load(self, skills_dir: Path) -> dict[tuple[str, int], Skill]:
        """Validate every YAML under `skills_dir` and return the keyed dict.

        Raises `SkillValidationFailedError` aggregating all discovered
        problems when `skills_dir` is missing, any file fails to validate,
        or two files collide on the same `(name, version)` key. Emits a
        `skill_manifest_empty` warning and returns an empty dict when
        `skills_dir` exists but contains no two-level YAML files.
        """
        if not skills_dir.is_dir():
            raise SkillValidationFailedError(
                file=str(skills_dir),
                reason=f"skills_dir '{skills_dir}' does not exist or is not a directory",
            )

        loaded: dict[tuple[str, int], Skill] = {}
        origins: dict[tuple[str, int], Path] = {}
        problems: list[str] = []

        for path in sorted(skills_dir.glob("*/*.yaml")):
            if not path.is_file():
                continue
            try:
                schema = SkillYamlSchema.load_from_file(path)
            except SkillValidationFailedError as exc:
                reason = _reason_of(exc)
                problems.append(f"{path}: {reason}")
                continue
            except Exception as exc:  # noqa: BLE001 — intentional aggregation
                problems.append(f"{path}: {type(exc).__name__}: {exc}")
                continue

            parent_name = path.parent.name
            if parent_name != schema.name:
                problems.append(
                    f"{path}: directory name '{parent_name}' does not match "
                    f"body name '{schema.name}'",
                )
                continue

            key = (schema.name, schema.version)
            if key in origins:
                problems.append(
                    f"duplicate skill ({schema.name}, {schema.version}) defined by "
                    f"{origins[key]} and {path}",
                )
                continue

            origins[key] = path
            loaded[key] = Skill.from_schema(schema, default_docling=self._default_docling)

        if problems:
            raise SkillValidationFailedError(
                file=str(skills_dir),
                reason="\n".join(problems),
            )

        if not loaded:
            _logger.warning("skill_manifest_empty", skills_dir=str(skills_dir))

        return loaded


def _reason_of(exc: SkillValidationFailedError) -> str:
    if exc.params is None:
        return str(exc)
    dumped = exc.params.model_dump()
    value = dumped.get("reason", "")
    return value if isinstance(value, str) else str(value)
