"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class SkillNotFoundParams(BaseModel):
    """Parameters for SKILL_NOT_FOUND error."""

    name: str
    version: str
