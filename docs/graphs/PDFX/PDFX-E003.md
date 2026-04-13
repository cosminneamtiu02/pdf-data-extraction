---
type: epic
id: PDFX-E003
parent: PDFX
title: PDF Parsing Layer
status: not-started
priority: 30
dependencies: [PDFX-E001]
---

## Short description

Wrap Docling behind a thin `DocumentParser` protocol that emits plain `TextBlock` data, handle both native digital and scanned PDFs (OCR engaged automatically), enforce the 200-page limit early, and raise clean domain errors for invalid, password-protected, or empty PDFs — containing every Docling import in a single implementation file.

## Description

This Epic owns the entire `features/extraction/parsing/` subpackage. It defines an internal `DocumentParser` protocol with a single method (`async parse(pdf_bytes, docling_config) -> ParsedDocument`) and a concrete `DoclingDocumentParser` that is the only file in the feature permitted to import Docling types. The parser walks Docling's `DoclingDocument` output and emits a `ParsedDocument` containing a list of `TextBlock` dataclasses — each with `text`, 1-indexed `page_number`, `BoundingBox` in PDF page coordinates, and a stable `block_id` — plus a `page_count` field. The same code path handles native digital PDFs (text layer extracted directly) and scanned PDFs (Docling's OCR pipeline engaged automatically when no text layer is found). Four error modes are raised as domain errors: `PDF_INVALID` (400) for corrupted or unreadable bytes; `PDF_PASSWORD_PROTECTED` (400) for encrypted PDFs; `PDF_TOO_MANY_PAGES` (413) evaluated as soon as page count is known, before OCR or layout analysis runs; and `PDF_NO_TEXT_EXTRACTABLE` (422) when both native parsing and OCR attempts yield zero text blocks. The parser also applies the skill's optional `docling:` configuration section, merged over the global defaults from `Settings.docling_*`.

## Rationale

This Epic exists because coordinate matching, LangExtract orchestration, and annotation all operate on plain `TextBlock` data structures that the feature owns — not on Docling's native types. Wrapping Docling behind a thin, protocol-shaped boundary makes the coordinate matching layer unit-testable against fake `TextBlock` instances without running Docling or parsing real PDFs, and keeps Docling's evolving API surface confined to one file that can be updated independently. It traces to multiple project success criteria: **"native digital PDF extraction latency"** and **"scanned PDF extraction latency"** (Docling's OCR pipeline is the dominant cost for the scanned profile), and **"hard limits"** (the 200-page cap is enforced inside this layer to avoid OCR cost explosions). Without this Epic, the pipeline cannot feed any text to LangExtract at all — it is a leaf dependency of everything downstream.

## Boundary

**In scope:** the entire `features/extraction/parsing/` subpackage (`document_parser.py`, `docling_document_parser.py`, `parsed_document.py`, `text_block.py`, `bounding_box.py`); Docling invocation with merged global + per-skill configuration; OCR engagement via Docling's default OCR pipeline (auto-detected when no text layer exists); all four PDF-side error code raises; `import-linter` contract containing Docling imports to `docling_document_parser.py` only; integration tests against a small corpus of fixture PDFs (native digital + scanned + corrupted + encrypted + blank).

**Out of scope:** text concatenation across blocks (that's PDFX-E005 — `TextConcatenator` lives in the coordinate matching layer, not the parsing layer); any byte-size check (that's PDFX-E006, enforced at the router level before parsing is even attempted); tuning Docling's OCR engine beyond what the global defaults and per-skill overrides express (operators pick OCR parameters via `Settings.docling_*`, not by modifying the parser); any drawing on PDFs (that's PDFX-E006, `PdfAnnotator`).

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- Whether password-protected PDF detection happens via PyMuPDF preflight (reusing the `fitz` dependency that ships for annotation) or via Docling's own error surface. Default: use whichever raises a cleaner error first — probably Docling, since it's already being invoked. If it requires a PyMuPDF preflight, extend the `pymupdf` containment contract to allow imports in this file as well.
- Whether `page_count` should be part of `ParsedDocument` or carried separately through the pipeline metadata. Default: part of `ParsedDocument`.
- Exact mapping between Docling's internal coordinate system and PDF page coordinates (A-004 in the requirements spec assumes they're equivalent or trivially related). Resolved by a benchmark/spike during implementation; if non-trivial, document the transform in this Epic's parsing layer.
