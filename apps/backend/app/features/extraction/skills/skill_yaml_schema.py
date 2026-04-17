"""SkillYamlSchema — Pydantic validator for a single skill YAML file.

Deep validation is the whole point: authoring mistakes caught at parse time are
essentially free; at request time they are ruinous. This module enforces:

- Structural shape via Pydantic (name, version, prompt, examples, output_schema).
- `output_schema` is itself a valid JSONSchema (Draft 7 meta-validation).
- When `output_schema` declares `type: object` (explicitly or by implication
  when the `type` keyword is omitted, per JSONSchema Draft 7 semantics for
  object-shaped extraction results), it must declare at least one entry in
  `properties`. A zero-field object schema is structurally unable to produce
  an extraction result and would otherwise surface as a confusing deferred
  `STRUCTURED_OUTPUT_FAILED` at request time (issue #114).
- Every example's `output` satisfies `output_schema`.
- The filename integer (e.g. `2.yaml`) matches the body `version` field.

When multiple problems are present, they are aggregated into one
`SkillValidationFailedError` so the skill author sees them all in one pass.
"""

from pathlib import Path
from typing import Any, Self

import yaml
from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills.deep_freeze import thaw
from app.features.extraction.skills.skill_docling_config import SkillDoclingConfig
from app.features.extraction.skills.skill_example import SkillExample

_SLUG_PATTERN = r"^[a-z0-9][a-z0-9_\-]*$"


class SkillYamlSchema(BaseModel):
    """Parsed and validated skill YAML file.

    Use `SkillYamlSchema.load_from_file(path)` as the entry point — it performs
    the filename-vs-body version cross-check that raw Pydantic validation
    cannot do on its own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, pattern=_SLUG_PATTERN)
    version: int = Field(gt=0)
    description: str | None = None
    prompt: str = Field(min_length=1)
    examples: list[SkillExample] = Field(min_length=1)
    output_schema: dict[str, Any]
    docling: SkillDoclingConfig | None = None

    @model_validator(mode="after")
    def _validate_schema_and_examples(self) -> Self:
        problems: list[str] = []

        try:
            Draft7Validator.check_schema(self.output_schema)
        except SchemaError as exc:
            problems.append(
                f"output_schema is not a valid JSONSchema: {exc.message}",
            )
            # If the schema itself is broken, we cannot validate examples against it.
            # `file` is filled in by `load_from_file` when it re-raises with the
            # known path; inner-validator raises never escape unwrapped.
            raise SkillValidationFailedError(file="", reason="\n".join(problems)) from exc

        if _is_empty_object_schema(self.output_schema):
            problems.append(
                "output_schema must declare at least one entry in 'properties' "
                "for object-typed schemas; a zero-field schema cannot produce "
                "an extraction result",
            )
            # Examples against a zero-field schema carry no useful signal:
            # any non-empty example output would be a false-positive
            # violation of the (structurally empty) schema. Raise now.
            raise SkillValidationFailedError(file="", reason="\n".join(problems))

        validator = Draft7Validator(self.output_schema)
        for index, example in enumerate(self.examples):
            # jsonschema's type stubs expose `iter_errors` as a partially-unknown
            # Overload that pyright strict flags as reportUnknownMemberType;
            # binding the method locally lets us silence exactly that call
            # without hiding any behavior.
            iter_errors = validator.iter_errors  # type: ignore[reportUnknownMemberType]
            # SkillExample deep-freezes `output` (MappingProxyType + tuples)
            # at construction time.  jsonschema does not recognise
            # MappingProxyType as an ``"object"`` type, so we thaw the
            # frozen value back to plain dicts/lists for validation.
            errors = sorted(iter_errors(thaw(example.output)), key=str)
            for error in errors:
                path_parts: list[str] = [str(p) for p in error.absolute_path]
                path = "/" + "/".join(path_parts)
                problems.append(
                    f"example index {index} violates output_schema at {path}: {error.message}",
                )

        if problems:
            raise SkillValidationFailedError(file="", reason="\n".join(problems))

        return self

    @classmethod
    def load_from_file(cls, path: Path) -> "SkillYamlSchema":
        """Load, parse, and fully validate a skill YAML file.

        Performs filename-vs-body version consistency checking in addition to
        the Pydantic + JSONSchema validation run by the model validator.
        """
        file_str = str(path)
        try:
            filename_version = int(path.stem)
        except ValueError as exc:
            msg = (
                f"skill filename stem '{path.stem}' is not an integer; "
                "skill files must be named '<integer>.yaml'"
            )
            raise SkillValidationFailedError(file=file_str, reason=msg) from exc

        raw_text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            msg = f"skill YAML is not parseable: {exc}"
            raise SkillValidationFailedError(file=file_str, reason=msg) from exc
        if not isinstance(data, dict):
            msg = "skill YAML did not parse to a mapping"
            raise SkillValidationFailedError(file=file_str, reason=msg)

        try:
            instance = cls.model_validate(data)
        except SkillValidationFailedError as exc:
            # Inner-validator raises carry `file=""`; fill in the known path.
            inner_reason = exc.params.model_dump()["reason"] if exc.params else str(exc)
            raise SkillValidationFailedError(
                file=file_str,
                reason=str(inner_reason),
            ) from exc

        if instance.version != filename_version:
            msg = (
                f"filename version {filename_version} does not match "
                f"body version {instance.version}"
            )
            raise SkillValidationFailedError(file=file_str, reason=msg)

        return instance


def _is_empty_object_schema(schema: dict[str, Any]) -> bool:
    """Return True when `schema` is an object-typed schema with zero properties.

    Covers the three variants that Draft 7 meta-validation accepts but that
    cannot produce any extraction field (issue #114):

    - `{}` — wholly empty schema (treated as an object schema by our domain,
      since skills always return structured object output).
    - `{"type": "object"}` — type declared, `properties` absent.
    - `{"type": "object", "properties": {}}` — type declared, `properties`
      present but empty.

    Schemas with an explicit non-`object` `type` (e.g. `{"type": "string"}`)
    are outside the scope of this invariant — they would fail elsewhere if
    ever used for an extraction skill.
    """
    declared_type = schema.get("type", "object")
    if declared_type != "object":
        return False
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return True
    # `properties` here is typed `dict[Unknown, Unknown]` from `schema.get`'s
    # `dict[str, Any]` return; `bool(dict)` sidesteps pyright strict
    # complaining about `len(...)` needing a fully-parametrised argument.
    return not properties
