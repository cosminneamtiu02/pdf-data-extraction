"""Operator-facing scripts (not part of the app wheel).

Modules here are standalone entry points invoked via `uv run python -m
scripts.<name>` or wired through Taskfile targets. They must not import
`app.main`, FastAPI, Docling, LangExtract, PyMuPDF, or the Ollama HTTP
client — they exist specifically to bypass the full service boot.
"""
