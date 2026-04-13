# Runbook

Operational guide for running and maintaining the system.

## Local Development

```bash
# Start everything
task dev

# Backend only
task dev:backend

# Frontend only
task dev:frontend
```

### First-time setup (fresh clone)

```bash
# Start Postgres
task docker:up

# Generate and run initial migration
cd apps/backend
task db:revision -- "create_initial_tables"
task db:migrate
```

Services:
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs
- Storybook: `task storybook` -> http://localhost:6006

## Database

```bash
# Run migrations
task db:migrate

# Create a new migration
task db:revision -- "describe_change"

# Reset database (dev only)
task db:reset
```

## Testing

```bash
# All tests
task test

# Unit only (fast)
task test:unit

# Integration (needs Docker for Testcontainers)
task test:integration

# E2E (needs full stack running)
task test:e2e

# Contract tests
task test:contract
```

## Error System

```bash
# After editing errors.yaml:
task errors:generate
task errors:check
```

## Docker

```bash
# Build images
task docker:build

# Start stack
task docker:up

# Stop stack
task docker:down
```

## Health Checks

- Liveness: `GET /health` -> `{"status": "ok"}`
- Readiness: `GET /ready` -> `{"status": "ready"}` (checks DB)

## Troubleshooting

### Backend won't start
- Check `DATABASE_URL` in `.env`
- Ensure Postgres is running: `docker compose -f infra/compose/docker-compose.yml up db`
- Run migrations: `task db:migrate`

### Frontend won't start
- Run `pnpm install` in `apps/frontend/`
- Check Node version: must be 22+

### Tests fail with "connection refused"
- Integration tests need Docker running (for Testcontainers)
- E2E tests need the full stack: `task docker:up`
