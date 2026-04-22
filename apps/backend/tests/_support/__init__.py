"""Opt-in test helpers that would otherwise force heavy imports at conftest load.

Lives under ``tests/_support/`` (not ``tests/conftest.py``) so that only the
test modules that actually need a helper pay the import cost of the
production packages the helper depends on. Centralising these in
``conftest.py`` forced every test file pytest discovers to load the
transitive closure of every helper's dependencies -- a real drag on unit
test startup for tests that only touch coordinates, schemas, or the core
logger (issue #354).
"""
