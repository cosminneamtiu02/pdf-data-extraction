"""Architecture gate: `app.shared` must carry a non-empty module docstring.

CLAUDE.md Architecture section names `app/shared/` as "feature-agnostic
helpers" — a declared, load-bearing package that the `shared-no-features`
import-linter contract references by name. An empty
`apps/backend/app/shared/__init__.py` file leaves that declaration
undocumented in-tree and invites cargo-cult additions or accidental
deletion (issue #371). This test pins the docstring's presence so the
package always explains its own purpose to the next reader.
"""

from __future__ import annotations

import app.shared


def test_shared_package_has_non_empty_docstring() -> None:
    """`app.shared.__doc__` must describe the package's intended purpose."""
    doc = app.shared.__doc__
    assert doc is not None, "app.shared must define a module docstring"
    assert doc.strip() != "", "app.shared docstring must not be empty or whitespace-only"
