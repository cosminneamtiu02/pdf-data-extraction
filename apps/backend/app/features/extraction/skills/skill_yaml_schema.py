"""SkillYamlSchema — Pydantic validator for a single skill YAML file.

Deep validation is the whole point: authoring mistakes caught at parse time are
essentially free; at request time they are ruinous. This module enforces:

- Structural shape via Pydantic (name, version, prompt, examples, output_schema).
- `output_schema` is itself a valid JSONSchema (Draft 7 meta-validation).
- When `output_schema` permits object-shaped output — either via
  `type: object`, via a list-form `type` that contains `"object"`, or via
  the domain rule that an omitted `type` is treated as object-shaped for
  extraction skills (not a JSONSchema Draft 7 rule; Draft 7 leaves a missing
  `type` unconstrained) — it must declare at least one entry in `properties`.
  A zero-field object schema is structurally unable to produce an extraction
  result and would otherwise surface as a confusing deferred
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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills._duplicate_key_safe_loader import (
    DuplicateKeyDetectingSafeLoader,
)
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

        detected_composition_keys = _detect_unsupported_composition_root_keys(self.output_schema)
        if detected_composition_keys:
            problems.append(
                f"output_schema uses unsupported root key(s) "
                f"{detected_composition_keys!r}: composition (anyOf/oneOf/allOf) "
                f"or $ref at the root is not yet supported for extraction "
                f"skills. The engine derives field names strictly from "
                f"top-level 'properties' (see declared_field_names in "
                f"extraction_engine), so composition-rooted schemas would "
                f"load successfully but silently produce zero-field "
                f"extractions at runtime. Declare explicit 'properties' at "
                f"the root instead. Issue #289."
            )
            raise SkillValidationFailedError(file="", reason="\n".join(problems))

        if _is_empty_object_schema(self.output_schema):
            problems.append(
                "output_schema must declare at least one entry in 'properties' "
                "for object-typed schemas; a zero-field schema cannot produce "
                "an extraction result",
            )
            # Examples against a zero-field schema carry no useful signal:
            # a Draft 7 object schema with no declared `properties` (and no
            # `additionalProperties: false`) accepts arbitrary properties, so
            # non-empty example outputs would silently pass — a false negative.
            # Raise now so the author sees the structural defect directly.
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
            # Use the duplicate-key-detecting loader so two mapping entries
            # with the same key fail at parse time rather than silently
            # collapsing last-wins — see ``_duplicate_key_safe_loader.py``
            # and issue #208.
            data = yaml.load(raw_text, Loader=DuplicateKeyDetectingSafeLoader)  # noqa: S506  # custom SafeLoader subclass, not yaml.Loader
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
        except ValidationError as exc:
            # Pydantic raises its own `ValidationError` for field-shape problems
            # (missing fields, wrong types, failed constraints) BEFORE the
            # `@model_validator(mode="after")` above ever runs. Without this
            # branch that exception escapes `load_from_file` unwrapped, and
            # `SkillLoader`'s broad `except Exception` surfaces it to operators
            # as an unparseable raw-Pydantic dump. Wrap it here so every
            # load-time failure reaches the boot-log in the same curated
            # `file=<path> reason=<human-readable>` shape (issue #214).
            reason = _format_pydantic_errors(exc)
            raise SkillValidationFailedError(file=file_str, reason=reason) from exc

        if instance.version != filename_version:
            msg = (
                f"filename version {filename_version} does not match "
                f"body version {instance.version}"
            )
            raise SkillValidationFailedError(file=file_str, reason=msg)

        return instance


def _format_pydantic_errors(exc: ValidationError) -> str:
    """Collapse a `pydantic.ValidationError` into one human-readable line.

    Each inner error becomes ``<dotted.loc>: <msg>`` and the items are joined
    with ``"; "`` so the whole blob is a single log-friendly line. We
    deliberately DO NOT include the ``type=`` / ``input_value=`` /
    ``For further information visit https://errors.pydantic.dev/...``
    trailers that Pydantic's own ``__str__`` appends — those are useful for
    library debugging but are noise in a curated operator-facing message,
    and they were the exact signal of "this came from raw Pydantic" that
    issue #214 called out.
    """
    parts: list[str] = []
    for err in exc.errors():
        loc_tuple = err.get("loc", ())
        dotted_loc = ".".join(str(p) for p in loc_tuple) if loc_tuple else "<root>"
        msg = err.get("msg", "")
        parts.append(f"{dotted_loc}: {msg}")
    return "; ".join(parts)


_UNSUPPORTED_COMPOSITION_KEYS: tuple[str, ...] = ("anyOf", "oneOf", "allOf", "$ref")


def _detect_unsupported_composition_root_keys(schema: dict[str, Any]) -> list[str]:
    """Return the list of unsupported composition/$ref keys present at the schema root.

    Returns an empty list if none are present (truthy check at call sites
    doubles as "uses unsupported root shape"). The returned list preserves
    the declaration order in ``_UNSUPPORTED_COMPOSITION_KEYS`` so error
    messages are deterministic.

    Draft 7 permits these keys as valid schema roots, but the extraction
    engine's ``declared_field_names`` derives fields strictly from top-level
    ``properties``. A composition-rooted schema would load successfully and
    then silently produce zero-field extractions at runtime. Reject at load
    time with a clearer error than the generic "empty object" branch would
    emit (issue #289).
    """
    return [key for key in _UNSUPPORTED_COMPOSITION_KEYS if key in schema]


def _is_empty_object_schema(schema: dict[str, Any]) -> bool:
    """Return True when `schema` permits object output but declares zero properties.

    Covers the Draft 7 variants that meta-validation accepts but that cannot
    produce any extraction field (issue #114):

    - `{}` — wholly empty schema. Draft 7 treats this as unconstrained;
      this project treats an omitted `type` as object-shaped output for
      extraction skills, so a no-properties `{}` falls under this invariant.
    - `{"type": "object"}` — type declared, `properties` absent.
    - `{"type": "object", "properties": {}}` — type declared, `properties`
      present but empty.
    - `{"type": ["object", "null"]}` (or any list/tuple `type` containing
      `"object"`) — the schema still permits object-shaped output and so is
      subject to the same at-least-one-property rule.

    Schemas whose `type` does not permit `object` (e.g. `{"type": "string"}`,
    `{"type": ["string", "null"]}`) are outside the scope of this invariant —
    they would fail elsewhere if ever used for an extraction skill.

    Composition (`anyOf`/`oneOf`/`allOf`) and `$ref` roots are rejected
    by a separate guard in the model validator because the extraction
    engine only knows how to derive field names from top-level
    `properties`. See `_detect_unsupported_composition_root_keys`.
    """
    declared_type = schema.get("type")
    if declared_type is None:
        allows_object = True
    elif isinstance(declared_type, str):
        allows_object = declared_type == "object"
    elif isinstance(declared_type, (list, tuple)):
        # `declared_type` here is a list/tuple of unknown element types; the
        # membership test is safe regardless of element type.
        allows_object = "object" in declared_type
    else:
        # Any other shape (e.g. bool, dict) is not a valid JSONSchema `type`
        # and Draft 7 meta-validation would already have rejected it.
        allows_object = False

    if not allows_object:
        return False

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return True
    # `properties` here is typed `dict[Unknown, Unknown]` from `schema.get`'s
    # `dict[str, Any]` return; `bool(dict)` sidesteps pyright strict
    # complaining about `len(...)` needing a fully-parametrised argument.
    return not properties
