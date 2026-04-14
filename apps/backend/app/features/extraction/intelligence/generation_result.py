"""GenerationResult: the immutable carrier returned from a successful generation.

`data` is the parsed and schema-validated structured output. `attempts` is the
number of total tries the validator took to reach a valid result (1-4 by
default, bounded by `Settings.structured_output_max_retries + 1`). `raw_output`
is the final raw model response text, preserved for logging and debugging.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GenerationResult:
    data: dict[str, Any]
    attempts: int
    raw_output: str
