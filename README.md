# Project Template

Full-stack monorepo template with React frontend, FastAPI backend, and PostgreSQL.

## Tech Stack

- **Frontend**: React 19, Vite, TypeScript strict, TanStack Query/Router, Tailwind, shadcn/ui, Storybook, i18next
- **Backend**: Python 3.13, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, structlog
- **Database**: PostgreSQL 17
- **Testing**: Vitest + RTL + Playwright (FE), pytest + Testcontainers + Schemathesis (BE)
- **Tooling**: Taskfile, Biome, Ruff, Pyright, import-linter

## Quick Start

```bash
# Prerequisites: Python 3.13, Node 22, pnpm 10, Docker

# Install backend
cd apps/backend && uv sync --dev

# Install frontend
cd apps/frontend && pnpm install

# Start with Docker
task dev
```

## Commands

| Command | Description |
|---|---|
| `task dev` | Start full stack via docker-compose |
| `task check` | Run all linters, type checkers, and tests |
| `task test:unit` | Run unit tests (backend + frontend) |
| `task test:integration` | Run integration tests (Testcontainers) |
| `task test:e2e` | Run Playwright E2E tests |
| `task test:contract` | Run contract tests |
| `task lint` | Run all linters |
| `task format` | Run all formatters |
| `task storybook` | Start Storybook |
| `task db:migrate` | Run Alembic migrations |
| `task errors:generate` | Generate error classes from errors.yaml |
| `task errors:check` | Validate translations |

## Documentation

- [Architecture](docs/architecture.md)
- [Conventions](docs/conventions.md)
- [Decisions](docs/decisions.md)
- [Testing](docs/testing.md)
- [Runbook](docs/runbook.md)

## AI-Assisted Development

See [CLAUDE.md](CLAUDE.md) for the discipline contract governing AI-assisted work.

## License

See [LICENSE](LICENSE).
