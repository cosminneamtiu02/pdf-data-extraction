"""GenerationResult: the frozen-dataclass carrier returned from a successful generation.

`data` is the parsed and schema-validated structured output. `attempts` is the
number of total tries the validator took to reach a valid result (1-4 by
default, bounded by `Settings.structured_output_max_retries + 1`). `raw_output`
is the final raw model response text, preserved for logging and debugging.

The dataclass is `frozen=True`, which prevents reassigning fields on an existing
instance. Per Python convention this does not deep-freeze the `data` dict's
contents — callers are expected to treat the payload as read-only rather than
relying on runtime enforcement at depth.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GenerationResult:
    data: dict[str, Any]
    attempts: int
    raw_output: str
