"""Few-shot example embedded in a skill YAML file."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class SkillExample(BaseModel):
    """One few-shot example used to prime the extraction LLM.

    `input` is the raw text the model should treat as the document body for
    this demonstration. `output` is the structured dict the model should
    produce — it must conform to the enclosing skill's `output_schema`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: str
    output: dict[str, Any]
