"""Few-shot example embedded in a skill YAML file."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

from pydantic import BaseModel, ConfigDict


class SkillExample(BaseModel):
    """One few-shot example used to prime the extraction LLM.

    `input` is the raw text the model should treat as the document body for
    this demonstration. `output` is the structured dict the model should
    produce — it must conform to the enclosing skill's `output_schema`.

    The `output` dict is deep-frozen at construction time so that callers
    cannot silently mutate example outputs and change extraction prompts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: str
    output: Mapping[str, Any]

    def model_post_init(self, _context: Any, /) -> None:
        """Deep-freeze ``output`` after Pydantic validation finishes.

        Pydantic's ``frozen=True`` prevents attribute reassignment on the
        model, so we bypass it via ``object.__setattr__`` — the same
        pattern Pydantic itself uses in ``model_post_init``.
        """
        object.__setattr__(self, "output", _deep_freeze_mapping(self.output))


def _deep_freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recursively wrap nested mappings in ``MappingProxyType`` and lists in tuples."""
    return MappingProxyType({str(k): _freeze_any(v) for k, v in value.items()})


def _freeze_any(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _deep_freeze_mapping(cast("Mapping[str, Any]", value))
    if isinstance(value, list):
        return tuple(_freeze_any(item) for item in cast("list[Any]", value))
    return value
