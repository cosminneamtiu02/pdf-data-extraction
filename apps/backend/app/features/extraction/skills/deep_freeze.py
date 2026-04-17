"""Deep-freeze and thaw helpers for immutable nested structures.

Shared by `SkillExample` (freezes example outputs) and `Skill` (freezes
`output_schema`). Extracted to a single module to avoid duplicating the
recursion logic.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast


def deep_freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recursively wrap nested mappings in ``MappingProxyType`` and sequences in tuples."""
    return MappingProxyType({str(k): _freeze_any(v) for k, v in value.items()})


def _freeze_any(value: Any) -> Any:
    if isinstance(value, Mapping):
        return deep_freeze_mapping(cast("Mapping[str, Any]", value))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_any(item) for item in cast("list[Any] | tuple[Any, ...]", value))
    return value


def thaw(value: Any) -> Any:
    """Recursively convert frozen structures back to plain dicts/lists.

    ``SkillExample`` deep-freezes its ``output`` into ``MappingProxyType`` +
    tuples. jsonschema's Draft7Validator does not accept ``MappingProxyType``
    as ``"object"`` type, so we thaw just for validation.
    """
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, Any]", value)
        return {str(k): thaw(v) for k, v in mapping.items()}
    if isinstance(value, (list, tuple)):
        seq = cast("list[Any] | tuple[Any, ...]", value)
        return [thaw(item) for item in seq]
    return value
