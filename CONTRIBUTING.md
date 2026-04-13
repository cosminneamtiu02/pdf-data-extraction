# Contributing

## Getting Started

1. Clone the repository
2. Install prerequisites: Python 3.13, Node 22, pnpm 10, Docker
3. Run `task dev` to start the full stack

## Development Setup

See [docs/architecture.md](docs/architecture.md) for the system architecture
and [docs/conventions.md](docs/conventions.md) for coding conventions.

## Code Style

- **Python**: Ruff (ALL rules), Pyright strict
- **TypeScript**: Biome, tsc strict
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/)

## Pull Request Process

1. Create a feature branch from `main`
2. Write tests first (TDD)
3. Run `task check` before pushing
4. Open a PR against `main`
5. Ensure CI passes
6. Squash merge

## Architecture

Read [CLAUDE.md](CLAUDE.md) for the complete list of rules and forbidden patterns.
