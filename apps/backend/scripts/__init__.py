"""Operator-facing scripts (not part of the app wheel).

Modules here are standalone entry points invoked via `uv run python -m
scripts.<name>` or wired through Taskfile targets. They must not import
`app.main`, FastAPI, Docling, LangExtract, PyMuPDF, or the Ollama HTTP
client — they exist specifically to bypass the full service boot.

``BenchmarkSettings`` is re-exported here so cross-package consumers (e.g.
the ``.env.example`` parity test in ``tests/unit/core/``) can import it as
``from scripts import BenchmarkSettings`` without reaching into the
underscore-private ``scripts._benchmark_settings`` module. The underscore
path remains the implementation location; the re-export is the public
boundary for the one legitimate outside reader.
"""

from scripts._benchmark_settings import BenchmarkSettings

__all__ = ["BenchmarkSettings"]
