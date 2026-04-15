"""Shared vocabulary for Docling pipeline knobs.

Defined in `app.core` so both `Settings` (core) and `SkillDoclingConfig`
(features) can reference the same Literal aliases without `core` having to
import from `features` — which the `shared-no-features` import-linter
contract forbids.

The vocabulary itself is authoritative: the parser layer's `DoclingConfig`
enforces the same strings at runtime, and every layer in between is closed-
shape on these aliases so a typo fails at the earliest possible boundary.
"""

from typing import Literal

OcrMode = Literal["auto", "force", "off"]
TableMode = Literal["fast", "accurate"]
