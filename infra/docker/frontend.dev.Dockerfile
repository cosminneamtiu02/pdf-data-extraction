# Dev-only frontend Dockerfile. Installs deps at build time so the container
# doesn't need network access at runtime.
# Build context must be the monorepo root.
FROM node:22-alpine

RUN npm install -g pnpm@10.11.0

WORKDIR /app

# Copy workspace config and lockfile first (cache layer)
COPY pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/frontend/package.json apps/frontend/
COPY packages/api-client/package.json packages/api-client/
COPY packages/error-contracts/package.json packages/error-contracts/

RUN pnpm install --frozen-lockfile

# Source will be bind-mounted over apps/frontend for hot reload.
WORKDIR /app/apps/frontend

EXPOSE 5173

CMD ["pnpm", "dev", "--host", "0.0.0.0"]
