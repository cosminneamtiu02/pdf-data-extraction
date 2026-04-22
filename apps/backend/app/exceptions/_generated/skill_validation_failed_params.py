"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class SkillValidationFailedParams(BaseModel):
    """Parameters for SKILL_VALIDATION_FAILED error."""

    file: str
    reason: str
