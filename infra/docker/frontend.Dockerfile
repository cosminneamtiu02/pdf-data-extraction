# ── Build stage ──────────────────────────────────────────────
# Build context must be the monorepo root (context: ../.. in docker-compose)
FROM node:22-alpine AS builder

RUN npm install -g pnpm@10.11.0

WORKDIR /repo

# Copy workspace config + lockfile first for layer caching
COPY pnpm-workspace.yaml pnpm-lock.yaml ./

# Copy package.json files for all workspace members
COPY apps/frontend/package.json apps/frontend/package.json
COPY packages/api-client/package.json packages/api-client/package.json
COPY packages/error-contracts/package.json packages/error-contracts/package.json

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy application source
COPY apps/frontend/ apps/frontend/
COPY packages/ packages/

# Build from the frontend workspace
RUN pnpm --filter @repo/frontend build

# ── Runtime stage ────────────────────────────────────────────
FROM nginx:alpine

# Copy built assets
COPY --from=builder /repo/apps/frontend/dist /usr/share/nginx/html

# Nginx config for SPA routing + API proxy
COPY infra/docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
