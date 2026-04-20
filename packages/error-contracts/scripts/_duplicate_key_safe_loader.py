"""PyYAML ``SafeLoader`` subclass that rejects duplicate mapping keys.

PyYAML's default ``SafeLoader`` silently collapses repeated mapping keys
last-wins, with no warning. ``errors.yaml`` is the source of truth for
every error code that ships in generated Python, TypeScript, and
translation-required-keys artifacts; a copy-paste typo that produces two
entries with the same top-level code, the same nested ``params``-map
name, or the same quoted-then-bare key would otherwise emit code that
drops one definition on the floor and deploys cleanly.

The pattern is intentionally identical to
``app.features.extraction.skills._duplicate_key_safe_loader`` (added in
PR #221 for skill YAMLs): override the default mapping constructor so
the duplicate is raised at parse time as a ``ConstructorError`` — a
``yaml.YAMLError`` subclass — rather than discovered later when the
codegen loops over keys that silently merged (issue #294).
"""

from __future__ import annotations

from collections.abc import Hashable
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
    overwrite the first value with the second. The raised error carries
    the duplicate key's source location so the author can jump straight
    to it.
    """
    # Parity with PyYAML's default ``SafeConstructor.construct_mapping``:
    # flatten the node first so YAML merge keys (``<<: *anchor``) and
    # related normalization are applied before we walk ``node.value``.
    # Our contract only replaces the duplicate-detection step —
    # everything else about mapping construction must behave exactly
    # like the upstream loader.
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    context = "while constructing a mapping"
    # PyYAML's stubs expose ``construct_object`` with an ``Unknown``
    # return, which pyright strict flags as partially-unknown. The
    # behaviour is well-defined (returns the Python object for the
    # node); the silences sit on the two call sites where pyright cannot
    # recover the type through the third-party stubs.
    construct_object = loader.construct_object  # type: ignore[reportUnknownMemberType]
    for key_node, value_node in node.value:
        key: Any = construct_object(key_node, deep=deep)  # type: ignore[reportUnknownVariableType]
        # Mirror PyYAML upstream: reject unhashable keys (e.g. a
        # sequence used as a mapping key via ``? [a, b]`` syntax) with a
        # curated ``ConstructorError`` rather than letting
        # ``key in mapping`` raise a raw ``TypeError`` that bypasses the
        # caller's ``except yaml.YAMLError`` envelope.
        if not isinstance(key, Hashable):
            raise yaml.constructor.ConstructorError(
                context,
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            )
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

    Use instead of ``yaml.safe_load`` wherever a YAML author's intent
    would be silently erased by PyYAML's default-loader behaviour. Raises
    ``yaml.constructor.ConstructorError`` (a ``yaml.YAMLError``
    subclass) so any ``except yaml.YAMLError`` handler catches it
    unchanged.
    """


DuplicateKeyDetectingSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_rejecting_duplicates,
)
