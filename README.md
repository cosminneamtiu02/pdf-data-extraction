# PDF Data Extraction

Self-hosted PDF data extraction microservice. Callers POST a PDF, a skill name, a skill version, and an output mode to a FastAPI endpoint; the service parses the PDF with Docling, orchestrates extraction via LangExtract against a locally running Gemma 4 model through Ollama, maps extracted values back to PDF-page coordinates, optionally draws PyMuPDF highlights, and returns structured JSON, an annotated PDF, or both. Stateless, no database, no cloud runtime dependencies.

See [`docs/superpowers/specs/`](docs/superpowers/specs/) for the full design and requirements specs, and [`docs/graphs/PDFX/`](docs/graphs/PDFX/) for the decomposed epics and features.

## Tech Stack

- **Runtime:** Python 3.13, FastAPI, Pydantic v2, pydantic-settings, structlog
- **Extraction:** Docling (PDF parsing + OCR), LangExtract (orchestration), Ollama + Gemma 4 smallest variant (local LLM), PyMuPDF (annotation)
- **Testing:** pytest + pytest-asyncio, schemathesis (contract), import-linter (architecture)
- **Tooling:** Taskfile, Ruff, Pyright, uv

Ollama runs on the host machine (outside the service container) and is reached via `host.docker.internal:11434` or `http://localhost:11434`. The service does not ship Ollama.

## Quick Start

Prerequisites:

- Python 3.13
- `uv` (Astral)
- Docker (optional — only if you want to run the service in a container)
- Ollama installed on the host, with the smallest Gemma 4 variant pulled (`ollama pull <tag>`)

```bash
# Install backend
cd apps/backend && uv sync --dev

# Run the backend directly with hot reload
task dev:backend

# Or start it via docker-compose (Ollama still runs on the host)
task dev
```

The service listens on port 8000. Liveness probe at `GET /health`; readiness at `GET /ready`.

## Commands

| Command | Description |
|---|---|
| `task dev` | Start the backend via docker-compose |
| `task dev:backend` | Start the backend directly with hot reload |
| `task check` | Run lint, type checker, architecture contracts, tests, error contracts |
| `task test:unit` | Run unit tests |
| `task test:integration` | Run in-process integration tests |
| `task test:contract` | Run contract tests against the OpenAPI spec |
| `task lint` | Run ruff |
| `task format` | Run ruff format |
| `task errors:generate` | Generate error classes from `packages/error-contracts/errors.yaml` |

## Documentation

- [Architecture](docs/architecture.md)
- [Conventions](docs/conventions.md)
- [Decisions](docs/decisions.md)
- [Testing](docs/testing.md)
- [Runbook](docs/runbook.md)
- [Design spec](docs/superpowers/specs/2026-04-13-pdf-extraction-microservice-design.md)
- [Requirements spec](docs/superpowers/specs/2026-04-13-pdf-extraction-microservice-requirements.md)
- [Graph tree (epics + features)](docs/graphs/PDFX/)

## AI-Assisted Development

See [CLAUDE.md](CLAUDE.md) for the discipline contract governing AI-assisted work on this project.

## License

See [LICENSE](LICENSE).
