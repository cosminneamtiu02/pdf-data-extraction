"""CorrectionPromptBuilder: assembles the retry prompt sent after a failure.

The wording is the minimal template suggested by the spec (OQ-004): reiterate
the schema, show the model its own malformed output, and ask for a corrected
response. The exact phrasing is intentionally brief and is expected to be
tuned empirically during PDFX-E004-F002's integration tests against Gemma 4.
"""

import json
from collections.abc import Mapping
from typing import Any


class CorrectionPromptBuilder:
    def build(
        self,
        original_prompt: str,
        malformed_output: str,
        output_schema: Mapping[str, Any],
        failure_reason: str,
    ) -> str:
        # ``json.dumps`` accepts any ``Mapping`` via its default encoder path,
        # so widening beyond ``dict`` costs nothing at runtime and lets us
        # accept the ``MappingProxyType`` that ``Skill.output_schema`` uses.
        schema_json = json.dumps(dict(output_schema), indent=2, sort_keys=True)
        return (
            f"{original_prompt}\n\n"
            "The previous response was not valid JSON matching the required schema.\n"
            f"Previous output:\n{malformed_output}\n\n"
            f"Validation failure:\n{failure_reason}\n\n"
            f"Expected schema:\n{schema_json}\n\n"
            "Return only a valid JSON object matching the schema. "
            "Do not include any commentary, markdown fences, or surrounding prose."
        )
