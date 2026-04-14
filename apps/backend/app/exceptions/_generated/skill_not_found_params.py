"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class SkillNotFoundParams(BaseModel):
    """Parameters for SKILL_NOT_FOUND error."""

    name: str
    version: str
