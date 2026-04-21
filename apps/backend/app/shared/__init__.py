"""Feature-agnostic helpers for the backend.

Modules placed here must stay domain-neutral: they cannot import from
`app.features.*` because they are shared infrastructure that every feature
can depend on. The `shared-no-features` import-linter contract in
`apps/backend/architecture/import-linter-contracts.ini` enforces this
direction statically, and the architecture tests under
`apps/backend/tests/unit/architecture/` cover the dynamic-import gaps.

See CLAUDE.md (Architecture: Backend Vertical Slices) and
`docs/architecture.md` for the full layering contract.
"""
