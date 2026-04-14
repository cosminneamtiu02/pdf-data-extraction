"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.skill_not_found_params import SkillNotFoundParams
from app.exceptions.base import DomainError


class SkillNotFoundError(DomainError):
    """Error: SKILL_NOT_FOUND."""

    code: ClassVar[str] = "SKILL_NOT_FOUND"
    http_status: ClassVar[int] = 404

    def __init__(self, *, name: str, version: str) -> None:
        super().__init__(params=SkillNotFoundParams(name=name, version=version))
