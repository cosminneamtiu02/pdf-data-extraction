"""Single entry point for regenerating every error-contracts artifact.

Extracted from the inlined ``python -c '...'`` blocks that previously
lived in ``Taskfile.yml`` (the ``errors:generate`` task) and in
``.github/workflows/ci.yml`` (the "Regenerate error contracts" step).
Those two copies were near-identical but drifted independently; see
issue #365 for the duplication diagnosis.

Issue #372 then added a proper ``argparse`` CLI directly on
``scripts.generate`` and pointed ``task errors:generate`` at the new
entry point. CI still calls::

    uv run --with pyyaml python -m scripts.generate_all

for backwards compatibility with the workflow yaml. To keep local
(``scripts.generate``) and CI (``scripts.generate_all``) generation
mechanically byte-identical, this module is now a thin shim: both
``main()`` and ``_parse_args`` delegate to ``scripts.generate``, which
owns the canonical default paths, argparse parser, and three-function
generator sequence. PR #499 review flagged that duplicating defaults
here opened a drift window; delegating closes it.

``python -m scripts.generate_all`` still works from
``packages/error-contracts/`` (or any environment where that directory
is on ``sys.path``) with zero behavioural difference from
``python -m scripts.generate``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.generate import build_parser
from scripts.generate import main as generate_main


def main(
    errors_yaml: Path | None = None,
    python_dir: Path | None = None,
    typescript_path: Path | None = None,
    required_keys_path: Path | None = None,
) -> int:
    """Delegate to ``scripts.generate.main`` so the two entry points cannot drift.

    Kept as a named public function (rather than re-exporting
    ``generate.main`` directly) because ``test_generate_all_script.py``
    imports this name and because CI calls
    ``python -m scripts.generate_all`` by name. The function signature
    is kept identical to ``generate.main`` so callers that pass keyword
    arguments continue to work unchanged.
    """
    return generate_main(
        errors_yaml=errors_yaml,
        python_dir=python_dir,
        typescript_path=typescript_path,
        required_keys_path=required_keys_path,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Reuse the shared ``scripts.generate`` parser so flags can't diverge."""
    return build_parser().parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    sys.exit(
        main(
            errors_yaml=args.errors_yaml,
            python_dir=args.python_dir,
            typescript_path=args.typescript_path,
            required_keys_path=args.required_keys_path,
        )
    )
