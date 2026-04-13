"""DomainError base class — the root of all errors that cross the API boundary.

Subclasses are generated from packages/error-contracts/errors.yaml.
Do not subclass DomainError directly in application code — edit errors.yaml
and run `task errors:generate` instead.
"""

from typing import ClassVar

from pydantic import BaseModel


class DomainError(Exception):
    """Base class for all domain errors.

    Each subclass has:
    - code: machine-readable error code (e.g. "WIDGET_NOT_FOUND")
    - http_status: HTTP status code to return
    - params: typed parameter object (or None for parameterless errors)
    """

    code: ClassVar[str]
    http_status: ClassVar[int]

    def __init__(self, *, params: BaseModel | None = None) -> None:
        self.params = params
        # Only expose code in exception args — never user params (PII risk)
        super().__init__(self.code)
