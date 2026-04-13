---
type: epic
id: PDFX-E005
parent: PDFX
title: Coordinate Matching
status: fully-detailed
priority: 50
dependencies: [PDFX-E003, PDFX-E004]
---

## Short description

Bridge Docling's `ParsedDocument` and LangExtract's `RawExtraction`s into per-field `ExtractedField` objects with accurate source-grounded bounding boxes — via text concatenation with an offset index, a three-step sub-block matcher that tolerates whitespace and Unicode normalization drift, and a span resolver that handles multi-block, cross-page, and ungrounded cases.

## Description

This Epic owns the entire `features/extraction/coordinates/` subpackage: `TextConcatenator`, `OffsetIndex`, `SubBlockMatcher`, and `SpanResolver`. The concatenator joins `ParsedDocument.blocks` into a single text string using a configurable separator (default `"\n\n"`) and simultaneously builds an `OffsetIndex` — an ordered list of `(start_offset, end_offset, block_id)` entries, one per block — that supports O(log n) binary-search lookup from a character offset back to its source block and within-block offset. The `SubBlockMatcher` attempts to locate an extracted value substring inside a block's text via a three-step fallback chain: (1) direct substring search, (2) whitespace-normalized search (collapsing runs of whitespace in both strings), (3) Unicode NFKC normalized search. On success, a tight sub-block bounding box is computed via character-offset ratios against the block's `BoundingBox`. On failure, the whole-block bounding box is returned. The `SpanResolver` is the top-level orchestrator: for each `RawExtraction` it looks up the start and end blocks in the `OffsetIndex`, dispatches to `SubBlockMatcher` for single-block spans, returns multiple `BoundingBoxRef` entries for multi-block and cross-page spans (one per touched block), and returns `grounded=false` with empty `bbox_refs` when no block contains the offsets (hallucinated or fully inferred values). Every output is an `ExtractedField` carrying `name`, `value`, `status`, `source` (document / inferred), `grounded`, and `bbox_refs`.

## Rationale

This Epic exists because the service's differentiating value proposition is *source-grounded highlights*: "here's the extracted value AND the exact place on the page it came from." Without accurate coordinate matching, the service degenerates to any other LLM extraction tool. The challenge is that LangExtract's character offsets are into the concatenated text string that `TextConcatenator` built, not into Docling's native page-coordinate blocks — the bridge has to be custom code because no off-the-shelf library knows about both sides of the pipeline. The Epic owns the coordinate bridge end-to-end. It also owns the defense against *normalization drift* — the common failure mode where Docling's whitespace handling, ligature rendering, or Unicode form differs from the text LangExtract was prompted against, causing character offsets to miss even when the value is visually obvious. The three-step `SubBlockMatcher` fallback is the concrete defense. It traces to project success criteria **"coordinate grounding rate ≥ 95% (native) / ≥ 85% (scanned)"** (the whole Epic exists to hit this number), and **"API stability — every declared field present with bbox refs"** (via the `grounded=false` fallback that guarantees a field is still returned even when coordinates can't be resolved).

## Boundary

**In scope:** the entire `features/extraction/coordinates/` subpackage (`text_concatenator.py`, `offset_index.py`, `sub_block_matcher.py`, `span_resolver.py`); the character-offset-to-block-id bridge logic; the three-step match fallback chain; multi-block and cross-page span handling; the `grounded=false` hallucinated-value path; unit tests for every class operating on synthetic `TextBlock` lists with zero Docling runtime dependency.

**Out of scope:** producing `TextBlock`s (that's PDFX-E003); producing `RawExtraction`s (that's PDFX-E004); drawing highlights on a PDF (that's PDFX-E006 — this Epic returns `BoundingBoxRef` data only, not rendered annotations); any visual/pixel-level precision concerns beyond character-offset ratios (per the requirements spec's A-006 assumption, character-offset ratios are deemed sufficient for v1, with visual inspection during benchmarking to verify).

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- Whether the `TextConcatenator` separator should be configurable per-skill or fixed globally. Default: fixed globally at `"\n\n"` for v1 — per-skill separator configuration is premature unless a real need surfaces.
- How `SpanResolver` handles the edge case where LangExtract returns a span with `start > end` (malformed offsets from the model). Default: treat as ungrounded (`grounded=false, bbox_refs=[]`).
- Whether the `BoundingBoxRef.page` field is 1-indexed or 0-indexed. Default: 1-indexed to match how humans read PDFs and what PyMuPDF expects for page lookups.
- Whether to pre-compute a trigram or similar index for `SubBlockMatcher` to speed up the normalized matches on large blocks. Default: no — direct string scans are fast enough for v1; optimize only if benchmarking shows otherwise.
