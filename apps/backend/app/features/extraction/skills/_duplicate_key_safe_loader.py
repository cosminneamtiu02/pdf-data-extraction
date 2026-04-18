"""PyYAML ``SafeLoader`` subclass that rejects duplicate mapping keys.

PyYAML's default ``SafeLoader`` silently collapses repeated mapping keys
last-wins, with no warning. In a skill YAML that is a footgun: a copy-paste
typo producing two ``amount_due`` entries under ``output_schema.properties``
reaches the Pydantic schema as ONE property. The schema validates, the
skill deploys, and the defect only surfaces at request time on a live
production call. Catching it at parse time is essentially free; this
loader wires that check into the YAML reading path (issue #208).
"""

from __future__ import annotations

from typing import Any

import yaml


def _construct_mapping_rejecting_duplicates(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    *,
    deep: bool = False,
) -> dict[Any, Any]:
    """Build a Python dict from a YAML mapping node, raising on duplicate keys.

    Runs before PyYAML's default mapping constructor would silently
    overwrite the first value with the second. The raised error carries the
    duplicate key's source location so the author can jump straight to it.
    """
    # Parity with PyYAML's default ``SafeConstructor.construct_mapping``:
    # flatten the node first so YAML merge keys (``<<: *anchor``) and related
    # normalization are applied before we walk ``node.value``. Our contract
    # only replaces the duplicate-detection step — everything else about
    # mapping construction must behave exactly like the upstream loader.
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    context = "while constructing a mapping"
    # PyYAML's stubs expose ``construct_object`` with an ``Unknown`` return,
    # which pyright strict flags as partially-unknown. The behavior is well-
    # defined (returns the Python object for the node) — the silences sit on
    # the two call sites where pyright cannot recover the type through the
    # third-party stubs.
    construct_object = loader.construct_object  # type: ignore[reportUnknownMemberType]
    for key_node, value_node in node.value:
        key: Any = construct_object(key_node, deep=deep)  # type: ignore[reportUnknownVariableType]
        if key in mapping:
            problem = f"duplicate key {key!r}"
            raise yaml.constructor.ConstructorError(
                context,
                node.start_mark,
                problem,
                key_node.start_mark,
            )
        mapping[key] = construct_object(value_node, deep=deep)  # type: ignore[reportUnknownVariableType]
    return mapping


class DuplicateKeyDetectingSafeLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` subclass that fails on duplicate mapping keys.

    Use instead of ``yaml.safe_load`` wherever a YAML author's intent would
    be silently erased by PyYAML's default-loader behavior. Raises
    ``yaml.constructor.ConstructorError`` (a ``yaml.YAMLError`` subclass),
    so existing ``except yaml.YAMLError`` handlers catch it unchanged.
    """


DuplicateKeyDetectingSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_rejecting_duplicates,
)
