"""Skill — immutable runtime domain object for a validated skill.

A `Skill` is what the extraction pipeline actually consumes. It is built from
a validated `SkillYamlSchema` via `Skill.from_schema`, which is also where the
skill's optional Docling override is merged against the caller-supplied
defaults — the resulting `docling_config` is always a concrete
`SkillDoclingConfig`, never a raw YAML dict.
"""

from dataclasses import dataclass, field
from typing import Any

from app.features.extraction.skills.skill_docling_config import SkillDoclingConfig
from app.features.extraction.skills.skill_example import SkillExample
from app.features.extraction.skills.skill_yaml_schema import SkillYamlSchema


@dataclass(frozen=True, slots=True)
class Skill:
    """Immutable, type-safe runtime handle for a validated skill."""

    name: str
    version: int
    description: str | None
    prompt: str
    examples: tuple[SkillExample, ...]
    output_schema: dict[str, Any]
    docling_config: SkillDoclingConfig = field(default_factory=SkillDoclingConfig)

    @classmethod
    def from_schema(
        cls,
        schema: SkillYamlSchema,
        *,
        default_docling: SkillDoclingConfig | None = None,
    ) -> "Skill":
        """Build a `Skill` from a validated `SkillYamlSchema`.

        If the schema declares a `docling` override, it takes precedence over
        the supplied `default_docling` on a per-field basis (override wins when
        non-None; otherwise the default's value is kept). If neither provides
        a value, the default `SkillDoclingConfig` is used.
        """
        base = default_docling or SkillDoclingConfig()
        override = schema.docling
        if override is None:
            merged = base
        else:
            merged = SkillDoclingConfig(
                ocr=override.ocr if override.ocr is not None else base.ocr,
                table_mode=(
                    override.table_mode if override.table_mode is not None else base.table_mode
                ),
            )

        return cls(
            name=schema.name,
            version=schema.version,
            description=schema.description,
            prompt=schema.prompt,
            examples=tuple(schema.examples),
            output_schema=schema.output_schema,
            docling_config=merged,
        )
