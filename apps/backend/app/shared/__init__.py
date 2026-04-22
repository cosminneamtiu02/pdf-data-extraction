"""Feature-agnostic helpers for the backend.

Modules placed here must stay domain-neutral: they cannot import from
`app.features.*` because they are shared infrastructure that every feature
can depend on. The `shared-no-features` import-linter contract in
`apps/backend/architecture/import-linter-contracts.ini` enforces this
direction for static imports. See the architecture tests under
`apps/backend/tests/unit/architecture/` for additional layering checks in
other scoped areas (e.g. the AST-scan gates on `app/features/extraction/`
and `app/api/` that cover dynamic imports those scans are scoped to).

See CLAUDE.md (Architecture: Backend Vertical Slices) and
`docs/architecture.md` for the full layering contract.
"""
