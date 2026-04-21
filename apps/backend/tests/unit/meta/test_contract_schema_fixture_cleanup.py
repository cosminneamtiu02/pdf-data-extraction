"""Meta-test for issue #352 — contract OpenAPI-schema fixture cleanup.

Issue #352: the prior module-level schema rebuild in
``tests/contract/test_schemathesis.py`` called ``tempfile.mkdtemp`` with
no finalizer, which leaked a skills directory under ``/tmp`` on every
pytest invocation (including bare ``--collect-only`` runs, since the
rebuild ran at import time rather than inside a fixture).

The fix moves the rebuild into a ``@pytest.fixture(scope="session")``
under ``tests/contract/conftest.py`` whose generator wraps a
``tempfile.TemporaryDirectory()`` context manager. The shared builder
is exposed as ``_build_extract_schema_session`` so this meta-test can
drive it directly — without spinning up a full pytest session — and
assert both halves of the contract:

1. During the yield, the tempdir exists and the schema is usable.
2. After the context exits, the tempdir is fully removed.

Driving the context manager directly (rather than via ``pytest.Pytester``
or a live session run) is intentional: ``Pytester``-based cleanup probes
depend on pytest's own ``tmp_path_factory`` retention policy (by default
the last three session roots are kept on disk), which would mask a real
finalizer-ordering bug. A pure ``TemporaryDirectory`` context manager has
deterministic semantics — the directory is unconditionally removed on
``__exit__`` — so verifying that the path no longer exists after the
``with`` block is a direct, honest cleanup assertion.
"""

from __future__ import annotations


def test_extract_schema_fixture_cleans_up_tempdir_after_session() -> None:
    """The session-scoped schema fixture must not leak its skills tempdir.

    Drives ``_build_extract_schema_session`` (the underlying context
    manager behind the ``extract_schema`` session fixture), captures
    the path of the tempdir it creates, then asserts the path is gone
    once the context exits. This is the load-bearing assertion for
    issue #352: the old code used ``tempfile.mkdtemp`` with no finalizer,
    so the directory persisted in ``/tmp`` after the process exited.
    """
    from tests.contract.conftest import _build_extract_schema_session

    with _build_extract_schema_session() as (schema, skills_dir):
        assert skills_dir.exists(), "skills tempdir should exist during the yield"
        assert skills_dir.is_dir(), "skills tempdir should be a directory"
        # The schema is load-bearing for every contract test that
        # depends on it — it must be fully constructed before the
        # fixture yields, so dependents can call `validate_response`.
        assert schema is not None

    # Contract: after the context manager exits, the tempdir is gone.
    # The old `tempfile.mkdtemp` path leaked here because no finalizer
    # was registered. A `TemporaryDirectory()` with a `with` block makes
    # the cleanup deterministic and this assertion meaningful.
    assert not skills_dir.exists(), (
        f"skills tempdir {skills_dir} was not cleaned up after the session "
        "context exited — the fixture is leaking a directory per pytest run "
        "(regression of issue #352)"
    )
