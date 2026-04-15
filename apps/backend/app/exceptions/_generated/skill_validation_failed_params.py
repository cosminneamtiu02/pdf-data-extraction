"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class SkillValidationFailedParams(BaseModel):
    """Parameters for SKILL_VALIDATION_FAILED error."""

    file: str
    reason: str
