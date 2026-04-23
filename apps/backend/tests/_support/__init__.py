"""Cross-tree test-support helpers.

Modules under ``tests/_support/`` host helpers that are shared across
multiple test levels (unit, integration, contract) and cannot live in a
single ``conftest.py`` because they must be importable by name, not
injected via fixtures. Keep this tree small and narrowly scoped: if a
helper is only needed by one test file, inline it there instead.
"""
