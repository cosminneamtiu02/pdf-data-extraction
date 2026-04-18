"""CorrectionPromptBuilder: assembles the retry prompt sent after a failure.

The wording is the minimal template suggested by the spec (OQ-004): reiterate
the schema, show the model its own malformed output, and ask for a corrected
response. The exact phrasing is intentionally brief and is expected to be
tuned empirically during PDFX-E004-F002's integration tests against Gemma 4.
"""

import json
from collections.abc import Mapping
from typing import Any, cast


def _thaw_for_json(value: Any) -> Any:
    """Recursively convert a (possibly deep-frozen) value to JSON-native types.

    ``Skill.output_schema`` is produced by ``deep_freeze_mapping`` and is a
    ``MappingProxyType`` whose nested values are themselves ``MappingProxyType``
    (and sequences are tuples). ``json.dumps`` does not accept arbitrary
    ``Mapping`` implementations — it only recognises plain ``dict`` — so any
    nested ``MappingProxyType`` reaches the encoder's ``default()`` path and
    raises ``TypeError``. Shallow-copying via ``dict(...)`` only thaws the
    top level, so we recurse.

    Duplicated (not imported) from ``skills.deep_freeze.thaw`` because the
    ``intelligence`` leaf subpackage is layer-independent from ``skills``
    per the C2a import-linter contract; the helper is tiny and stable, so
    the cost of duplication is lower than the cost of widening the layer
    boundary.
    """
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, Any]", value)
        return {str(k): _thaw_for_json(v) for k, v in mapping.items()}
    if isinstance(value, (list, tuple)):
        seq = cast("list[Any] | tuple[Any, ...]", value)
        return [_thaw_for_json(item) for item in seq]
    return value


class CorrectionPromptBuilder:
    def build(
        self,
        original_prompt: str,
        malformed_output: str,
        output_schema: Mapping[str, Any],
        failure_reason: str,
    ) -> str:
        # ``Skill.output_schema`` is a deep-frozen ``MappingProxyType`` with
        # nested ``MappingProxyType`` values, which ``json.dumps`` does not
        # accept. Recursively thaw to plain ``dict``/``list`` before dumping
        # so every nested level is JSON-serializable.
        schema_json = json.dumps(_thaw_for_json(output_schema), indent=2, sort_keys=True)
        return (
            f"{original_prompt}\n\n"
            "The previous response was not valid JSON matching the required schema.\n"
            f"Previous output:\n{malformed_output}\n\n"
            f"Validation failure:\n{failure_reason}\n\n"
            f"Expected schema:\n{schema_json}\n\n"
            "Return only a valid JSON object matching the schema. "
            "Do not include any commentary, markdown fences, or surrounding prose."
        )
