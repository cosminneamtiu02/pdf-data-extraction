# PDF Data Extraction Microservice — Design

- **Date:** 2026-04-13
- **Status:** Design approved, pending implementation plan
- **Scope:** v1 of a self-hosted PDF extraction microservice built as a new vertical slice inside the existing FastAPI backend, stripping out the template's database/widget/frontend layers in the same PR

---

## 1. Context and Goals

Build a fully self-hosted PDF data extraction microservice in Python. A caller POSTs a PDF, a skill name, a skill version, and an output mode to a FastAPI endpoint. The service parses the PDF with Docling, orchestrates extraction via LangExtract against a locally running Gemma 4 model through Ollama, optionally maps extraction results back to PDF coordinates via PyMuPDF highlights, and returns structured JSON, an annotated PDF, or both.

**External runtime dependencies:** only Ollama running locally on the developer machine, serving the smallest Gemma 4 variant available. No database. No frontend. No managed cloud services. No async job queue. All requests are synchronous. No authentication, multi-tenancy, rate limiting, or persistent storage at the service level.

This service is infrastructure intended to be embedded in larger projects. Callers will typically persist extraction results to their own database downstream.

## 2. Non-Goals (v1)

- No authentication or authorization.
- No persistent storage of any kind.
- No async job queue.
- No web UI or interactive visualization.
- No multi-tenancy.
- No rate limiting at the microservice level.
- No model fine-tuning.
- No support for non-PDF input formats.
- No LangExtract HTML visualization feature exposed.
- No API versioning beyond the initial `/api/v1/extract` route.

## 3. Key Architectural Decisions

Each decision is final for v1. Alternatives considered and rejected are noted where the choice was non-obvious.

### 3.1 Intelligence interface boundary

The abstract LLM boundary sits at the **model provider level inside LangExtract**. LangExtract itself is a fixed dependency — its chunking, multi-pass, and source-grounding behavior is always present. The swap point is a single `IntelligenceProvider` implementation file. A future Claude or GPT-4 provider is a new file conforming to the same protocol; nothing else in the extraction pipeline changes.

*Rejected:* wrapping LangExtract itself behind an abstract "extraction engine" boundary. LangExtract does real orchestration work that a hypothetical replacement would have to reimplement; the interface belongs one level down.

### 3.2 Skill system versioning

**Integer increments** (`v1`, `v2`, ...) with a `latest` alias resolved at load time to the highest integer for a given skill name. Callers pass either an explicit integer or `latest` in the request.

*Rejected:* semver (overkill, the service owner and skill author are the same person); date-based (noisy in request payloads, no compatibility signal).

### 3.3 Skill validation strategy

**Hybrid manifest.** At container startup, every skill YAML is parsed, structurally validated against a pydantic model, and registered in an in-memory `SkillManifest`. A broken skill file prevents container startup. Runtime `load(name, version)` calls are O(1) dict lookups with zero disk I/O. Authoring mistakes (missing fields, malformed YAML, example-vs-schema mismatch) are caught at deploy time, not request time.

### 3.4 Coordinate matching granularity

**Sub-block word matching** for precise highlights. When an extracted value is a substring of a Docling block, `SubBlockMatcher` locates the value within the block's text and computes a tight bounding box using character-offset ratios against the block's bbox. Falls back to the whole-block bbox if the substring cannot be located.

### 3.5 Coordinate matching failure behavior

Every field in the skill's output schema is always present in the response. Field-level status combines three orthogonal flags:

- `status: "extracted" | "failed"` — whether structured output produced a value for this field after all retries.
- `source: "document" | "inferred"` — whether LangExtract grounded the value in document text or inferred it from model world knowledge.
- `grounded: bool` — whether coordinate matching produced at least one bounding box.

Coordinate matching follows a fallback chain: sub-block match → whole-block match → `grounded: false` with empty `bbox_refs`. Callers get maximum information at every precision level.

### 3.6 Docling pipeline configuration

**Hybrid global + per-skill override.** Container starts with sensible global defaults from environment variables. A skill YAML may include an optional `docling:` section that overrides the defaults for that skill. Most skills omit the section and inherit defaults; specialized skills opt in.

### 3.7 Structured output compensation for Gemma 4

Parsing, cleanup, and retry logic lives in a **shared provider-agnostic `StructuredOutputValidator`**. Providers return raw model output text and hand it, together with the expected JSON schema, to the validator. The validator strips markdown fences, extracts the first JSON object from prose wrappers, validates against the schema, and retries up to **3 times (4 total attempts)** with a correction prompt that includes the malformed output and the schema. A future non-Gemma provider reuses the same utility without modification.

### 3.8 Partial extraction behavior

After all retries, if some fields parsed and validated while others did not, the response returns **200 OK** with per-field status flags. The JSON body always contains every field declared in the skill's output schema. Failed fields have `value: null` and `status: "failed"`. Only catastrophic failures (infrastructure errors, Ollama unavailable, no fields extractable at all) return non-2xx.

### 3.9 Ungrounded value handling

LangExtract distinguishes document-grounded extractions (value came from document text with a source span) from inferred extractions (value generated from model world knowledge). Both are included in the response, distinguished by the `source` field. Callers decide per-use-case whether to trust inferred values.

### 3.10 Docling abstraction layer

A thin `DocumentParser` protocol in `features/extraction/parsing/` wraps Docling. The concrete `DoclingDocumentParser` is the only file in the feature that imports Docling types. The rest of the extraction pipeline — concatenation, offset indexing, span resolution, annotation — operates exclusively on plain `TextBlock` and `ParsedDocument` data types owned by the feature. This makes the coordinate matching layer unit-testable without real PDFs or a Docling runtime and makes Docling itself swappable in principle.

### 3.11 "Both" output mode response format

`multipart/mixed` response with two parts: an `application/json` part carrying the structured result and an `application/pdf` part carrying the annotated PDF binary. Callers are developers building integration layers who will parse both parts and persist them to a database; cleanly separated content types serve that use case better than base64-in-JSON (bloat, decode step) or a temporary download token (violates the stateless contract).

## 4. Folder Structure

The extraction feature lives as a self-contained vertical slice at `apps/backend/app/features/extraction/`. One class per file. Layers ordered strictly: `router → service → {parsing, intelligence, extraction, skills, coordinates, annotation}`. No cross-feature imports. Concrete third-party types (`DoclingDocument`, Ollama HTTP client, PyMuPDF types) are confined to individual implementation files.

```
apps/backend/app/features/extraction/
├── router.py                           # POST /api/v1/extract — thin handler
├── service.py                          # ExtractionService — orchestrates the pipeline
│
├── schemas/
│   ├── extract_request.py              # ExtractRequest (form fields)
│   ├── extract_response.py             # ExtractResponse (structured JSON result shape)
│   ├── extracted_field.py              # ExtractedField
│   ├── bounding_box_ref.py             # BoundingBoxRef (page, x0, y0, x1, y1)
│   ├── extraction_metadata.py          # ExtractionMetadata
│   ├── output_mode.py                  # OutputMode enum (JSON_ONLY | PDF_ONLY | BOTH)
│   └── field_status.py                 # FieldStatus enum (extracted | failed)
│
├── parsing/                            # Docling abstraction
│   ├── document_parser.py              # DocumentParser protocol
│   ├── docling_document_parser.py      # DoclingDocumentParser — concrete impl
│   ├── parsed_document.py              # ParsedDocument (list[TextBlock])
│   ├── text_block.py                   # TextBlock (text, page_number, bbox, block_id)
│   └── bounding_box.py                 # BoundingBox internal value object
│
├── intelligence/                       # LLM provider abstraction
│   ├── intelligence_provider.py        # IntelligenceProvider protocol
│   ├── ollama_gemma_provider.py        # OllamaGemmaProvider — concrete impl
│   ├── structured_output_validator.py  # StructuredOutputValidator — shared retry/clean
│   ├── generation_result.py            # GenerationResult (data, attempts, raw_output)
│   └── correction_prompt_builder.py    # CorrectionPromptBuilder — builds retry prompts
│
├── extraction/                         # LangExtract orchestration
│   ├── extraction_engine.py            # ExtractionEngine — wraps LangExtract call
│   └── raw_extraction.py               # RawExtraction (value, char offsets, grounded flag)
│
├── skills/                             # Skill loader
│   ├── skill.py                        # Skill domain object
│   ├── skill_loader.py                 # SkillLoader — load by name+version
│   ├── skill_manifest.py               # SkillManifest — startup-validated registry
│   └── skill_yaml_schema.py            # SkillYamlSchema — pydantic validator
│
├── coordinates/                        # Coordinate matching layer
│   ├── offset_index.py                 # OffsetIndex — char offset → block
│   ├── text_concatenator.py            # TextConcatenator — joins blocks + builds index
│   ├── span_resolver.py                # SpanResolver — span → list[BoundingBoxRef]
│   └── sub_block_matcher.py            # SubBlockMatcher — locates value within a block
│
└── annotation/                         # PDF annotation layer
    └── pdf_annotator.py                # PdfAnnotator — draws highlights with PyMuPDF
```

Skill YAMLs live outside the Python package at repo-relative `apps/backend/skills/`:

```
apps/backend/skills/
├── invoice/
│   ├── 1.yaml
│   └── 2.yaml
└── research_paper/
    └── 1.yaml
```

The skills directory path is injected via `Settings.skills_dir` (pydantic-settings), defaulting to the above.

## 5. Component Responsibilities

### 5.1 Router (`router.py`)

Thin FastAPI handler. Declares `Depends()` for `ExtractionService`, parses `ExtractRequest` from multipart form fields, reads PDF bytes from `UploadFile`, calls `ExtractionService.extract(...)`, serializes the result per `output_mode`:

- `JSON_ONLY` → `JSONResponse(ExtractResponse)`
- `PDF_ONLY` → `Response(annotated_pdf_bytes, media_type="application/pdf")`
- `BOTH` → `multipart/mixed` response, hand-built via a small helper inlined in the router

No business logic. No conditionals beyond `output_mode` serialization.

### 5.2 ExtractionService (`service.py`)

Orchestrates the extraction pipeline. Single public method: `async extract(pdf_bytes, skill_name, skill_version, output_mode) -> ExtractionResult`. Steps:

1. `SkillLoader.load(skill_name, skill_version)` → `Skill`.
2. `DocumentParser.parse(pdf_bytes, docling_config=skill.docling_config)` → `ParsedDocument`.
3. `TextConcatenator.concatenate(parsed_document)` → `(concatenated_text, offset_index)`.
4. `ExtractionEngine.extract(concatenated_text, skill, intelligence_provider)` → `list[RawExtraction]`.
5. `SpanResolver.resolve(raw_extractions, offset_index, parsed_document)` → `list[ExtractedField]`. **Runs for every output mode, unconditionally**, because both the JSON response body and the PDF annotator depend on `ExtractedField.bbox_refs`.
6. If `output_mode != JSON_ONLY`: `PdfAnnotator.annotate(pdf_bytes, extracted_fields)` → `annotated_pdf_bytes`.
7. Return `ExtractionResult(fields, annotated_pdf_bytes | None, metadata)`.

The only genuine short-circuit is step 6 — `PdfAnnotator` is skipped for `JSON_ONLY`. Everything else runs in every mode. The router layer (§5.1) decides which parts of `ExtractionResult` to serialize: `JSON_ONLY` emits the JSON body and discards `annotated_pdf_bytes` (which is `None` anyway), `PDF_ONLY` emits only the annotated bytes, and `BOTH` emits both in a `multipart/mixed` response. See the requirements spec FR-039 for the corresponding acceptance criteria.

### 5.3 Parsing layer (`parsing/`)

- **`DocumentParser`** — protocol with one method: `async parse(pdf_bytes: bytes, docling_config: DoclingConfig) -> ParsedDocument`.
- **`DoclingDocumentParser`** — the only file that imports Docling. Invokes Docling with the provided config (including OCR settings), walks the resulting `DoclingDocument`, and emits a `ParsedDocument`. Raises `PdfInvalidError`, `PdfPasswordProtectedError`, or `PdfNoTextExtractableError` on failure.
- **`ParsedDocument`** — dataclass containing `blocks: list[TextBlock]` and `page_count: int`.
- **`TextBlock`** — dataclass with `text: str`, `page_number: int` (1-indexed), `bbox: BoundingBox`, `block_id: str`.
- **`BoundingBox`** — dataclass with `x0, y0, x1, y1` in PDF page coordinates (PDF origin bottom-left).

Everything downstream operates on these owned types.

### 5.4 Intelligence layer (`intelligence/`)

- **`IntelligenceProvider`** — `typing.Protocol` with one method: `async generate(prompt: str, output_schema: dict) -> GenerationResult`.
- **`OllamaGemmaProvider`** — concrete implementation that simultaneously satisfies two interfaces: our internal `IntelligenceProvider` protocol and LangExtract's community provider plugin contract (registered via LangExtract's plugin discovery mechanism). One class, two conformances. Holds an HTTP client for Ollama (base URL and model tag from `Settings`). LangExtract invokes this provider once per chunk it orchestrates; every invocation goes through the same path: send raw prompt to Ollama → collect raw response text → construct a `regeneration_callable` closing over the same Ollama client → delegate cleanup, parsing, validation, and retry to `StructuredOutputValidator.validate_and_retry(raw_text, output_schema, regeneration_callable)`. Default `Settings.ollama_model` is the smallest Gemma 4 variant available via Ollama (configurable, never hardcoded in source).
- **`StructuredOutputValidator`** — provider-agnostic utility:
  1. Clean: strip markdown code fences, trim leading/trailing prose, extract first JSON object.
  2. Parse: `json.loads`.
  3. Validate: `jsonschema.validate` against the expected schema.
  4. On any failure, invoke `regeneration_callable` with a correction prompt built by `CorrectionPromptBuilder`. The correction prompt includes the malformed output and a reminder of the expected schema.
  5. Retry up to 3 times (4 total attempts including the original).
  6. If all attempts fail, raise `StructuredOutputError` carrying the last raw output.
- **`GenerationResult`** — dataclass: `data: dict`, `attempts: int`, `raw_output: str`.
- **`CorrectionPromptBuilder`** — builds the correction prompt string from malformed output + schema.

The provider interface contract is "`prompt + schema → clean dict or raise`." Providers are thin; the validator owns the hardening.

### 5.5 Extraction layer (`extraction/`)

- **`ExtractionEngine`** — wraps the LangExtract invocation. Constructs LangExtract's function call parameters from the `Skill` (prompt, examples, output schema) and invokes LangExtract with the injected `IntelligenceProvider`. LangExtract's chunking/multi-pass/grounding behavior runs unchanged. Returns a `list[RawExtraction]`.
- **`RawExtraction`** — dataclass: `field_name: str`, `value: Any | None`, `char_offset_start: int | None`, `char_offset_end: int | None`, `grounded: bool` (from LangExtract's source grounding — `False` means inferred from model world knowledge).

### 5.6 Skills layer (`skills/`)

- **`SkillYamlSchema`** — pydantic model matching the YAML structure (name, version, prompt, examples, output_schema, docling). Used for structural validation at startup.
- **`Skill`** — runtime domain object containing the validated fields plus resolved Docling config (global defaults merged with skill overrides).
- **`SkillLoader`** — at startup, walks `Settings.skills_dir`, loads every `.yaml` file, validates via `SkillYamlSchema`, and registers in `SkillManifest`. At runtime, resolves `(name, version)` to a `Skill`.
- **`SkillManifest`** — in-memory registry keyed by `(name, version)`. Resolves `latest` to the highest integer version per skill name. Raises `SkillValidationError` at startup if any skill is malformed (container crashes). Raises `SkillNotFoundError` at runtime if an unknown skill is requested.

### 5.7 Coordinates layer (`coordinates/`)

- **`TextConcatenator`** — joins `TextBlock.text` values with a configurable separator (default `"\n\n"`) into one string. Simultaneously builds an `OffsetIndex` with one entry per block: `(start_offset, end_offset, block_id)`.
- **`OffsetIndex`** — ordered list of entries. Public method `lookup(char_offset: int) -> (block_id, offset_within_block) | None`. Binary search, O(log n).
- **`SubBlockMatcher`** — single method `locate(block_text: str, value: str) -> CharRange | None`. Attempts in order:
  1. Direct substring search.
  2. Whitespace-normalized search (collapse runs of whitespace in both strings).
  3. Unicode-normalized search (NFKC normalization on both strings).
  4. Returns `None` if all fail.
- **`SpanResolver`** — single method `resolve(raw_extraction, offset_index, parsed_document) -> ExtractedField`. Logic:
  1. Look up start and end blocks via `OffsetIndex`.
  2. If span is contained in one block: call `SubBlockMatcher.locate`; on success build a tight sub-block `BoundingBoxRef` via character-offset ratios; on failure fall back to the whole-block `BoundingBoxRef`.
  3. If span crosses multiple blocks: return **multiple** `BoundingBoxRef` entries, one per block. Page-boundary spans are a natural special case of this (different blocks may be on different pages).
  4. If no block contains the offset (hallucinated or fully inferred value): return `ExtractedField` with `grounded=False` and `bbox_refs=[]`.

### 5.8 Annotation layer (`annotation/`)

- **`PdfAnnotator`** — single method `annotate(pdf_bytes: bytes, fields: list[ExtractedField]) -> bytes`. Opens the PDF with PyMuPDF, iterates over each field's `bbox_refs`, draws a highlight annotation on the corresponding page at the specified coordinates, returns the serialized annotated PDF bytes. Fields with empty `bbox_refs` are skipped silently.

## 6. Data Shapes

### 6.1 Request

Multipart form fields:

- `pdf: UploadFile` — the PDF file.
- `skill_name: str` — e.g. `invoice`.
- `skill_version: str` — e.g. `1` or `latest`.
- `output_mode: OutputMode` — `JSON_ONLY | PDF_ONLY | BOTH`.

### 6.2 Skill YAML

```yaml
name: invoice
version: 1
description: Extract structured fields from invoices (PO numbers, totals, line items).

prompt: |
  Extract the following fields from the invoice text. Return a JSON object with exactly
  these keys. If a value is not present in the document, use null.

examples:
  - input: "Invoice #INV-2024-001\nTotal: $1,847.50\nDue: 2024-05-15"
    output:
      invoice_number: "INV-2024-001"
      total_amount: "$1,847.50"
      due_date: "2024-05-15"

output_schema:
  type: object
  required: [invoice_number, total_amount, due_date]
  properties:
    invoice_number: { type: string }
    total_amount:   { type: string }
    due_date:       { type: string, format: date }

docling:                    # optional, overrides global defaults
  ocr: auto
  table_mode: fast
```

Startup validation checks: YAML well-formedness, required fields present, `output_schema` is a valid JSONSchema object, every `examples[*].output` validates against `output_schema`, filename version matches body `version`.

### 6.3 Response (JSON part of `BOTH` or full body of `JSON_ONLY`)

```python
class ExtractResponse(BaseModel):
    skill_name: str
    skill_version: int              # resolved integer, never "latest"
    fields: dict[str, ExtractedField]
    metadata: ExtractionMetadata

class ExtractedField(BaseModel):
    name: str
    value: Any | None                                    # None when status == "failed"
    status: FieldStatus                                  # "extracted" | "failed"
    source: Literal["document", "inferred"]
    grounded: bool                                       # False if coordinate matching found nothing
    bbox_refs: list[BoundingBoxRef]                      # Empty if grounded is False

class BoundingBoxRef(BaseModel):
    page: int                                            # 1-indexed
    x0: float
    y0: float
    x1: float
    y1: float

class ExtractionMetadata(BaseModel):
    page_count: int
    duration_ms: int
    attempts_per_field: dict[str, int]
    parser_warnings: list[str]
```

Every key declared in the skill's `output_schema` is always present in `fields`. This invariant lets downstream callers write a stable database schema against the skill.

### 6.4 HTTP response per `output_mode`

- `JSON_ONLY` — `200 OK`, `Content-Type: application/json`, body is an `ExtractResponse`.
- `PDF_ONLY` — `200 OK`, `Content-Type: application/pdf`, body is the annotated PDF bytes. No JSON envelope.
- `BOTH` — `200 OK`, `Content-Type: multipart/mixed; boundary=<generated>`. Exactly two parts, in this order:
  1. **Part 1** — `Content-Type: application/json`, `Content-Disposition: form-data; name="result"`. Body is the `ExtractResponse` JSON.
  2. **Part 2** — `Content-Type: application/pdf`, `Content-Disposition: form-data; name="pdf"; filename="annotated.pdf"`. Body is the annotated PDF bytes.

The boundary is generated per-response. Callers parse the multipart body using any standard multipart parser (e.g. `requests-toolbelt`, `email.parser`, `httpx` helpers).

## 7. Error Contracts

New error codes added to `packages/error-contracts/errors.yaml` (generated via `task errors:generate`):

| Code | When raised | HTTP status |
|---|---|---|
| `SKILL_NOT_FOUND` | Skill name/version unknown to manifest | 404 |
| `SKILL_VALIDATION_FAILED` | Startup-time schema/YAML error — container refuses to start | N/A (crash) |
| `PDF_INVALID` | PyMuPDF/Docling cannot open the file | 400 |
| `PDF_TOO_LARGE` | PDF byte size exceeds `Settings.max_pdf_bytes` | 413 |
| `PDF_TOO_MANY_PAGES` | PDF page count exceeds `Settings.max_pdf_pages` | 413 |
| `PDF_PASSWORD_PROTECTED` | Encrypted PDF | 400 |
| `PDF_NO_TEXT_EXTRACTABLE` | Docling + OCR both produced empty text | 422 |
| `INTELLIGENCE_UNAVAILABLE` | Ollama unreachable or connect timeout | 503 |
| `INTELLIGENCE_TIMEOUT` | Per-request extraction budget exceeded | 504 |
| `STRUCTURED_OUTPUT_FAILED` | Four attempts exhausted, still no valid output for any field | 502 |

`PDF_TOO_LARGE` is raised in the router before the request hits `ExtractionService` — an early guard that rejects oversized uploads without allocating parsing resources. `PDF_TOO_MANY_PAGES` is raised inside `DoclingDocumentParser` as soon as the page count is known, before OCR or layout analysis runs.

Partial field failures (some fields extracted, some failed) are **not errors** — they return `200 OK` with per-field status flags. Only catastrophic failures raise `DomainError` subclasses.

Existing template CRUD error codes (`WIDGET_NOT_FOUND`, etc.) are removed from `errors.yaml`.

## 8. Configuration

New `Settings` fields (pydantic-settings, mirrored in `.env.example`):

- `skills_dir: Path = Path("apps/backend/skills")`
- `ollama_base_url: str = "http://host.docker.internal:11434"`
- `ollama_model: str = "<smallest Gemma 4 tag available via Ollama>"` — exact tag is a config value, never hardcoded in source
- `ollama_timeout_seconds: float = 30.0`
- `extraction_timeout_seconds: float = 180.0`
- `docling_ocr_default: str = "auto"`
- `docling_table_mode_default: str = "fast"`
- `structured_output_max_retries: int = 3`
- `max_pdf_bytes: int = 50 * 1024 * 1024`   # 50 MB
- `max_pdf_pages: int = 200`

All existing DB-related settings (`database_url`, etc.) are removed.

## 9. Testing Strategy

Three levels, all mandatory per CLAUDE.md's four-level convention minus the database-dependent integration level (no database exists). E2E is optional-slow.

### 9.1 Unit (`tests/unit/features/extraction/`)

Fast, no real PDFs, no Docling runtime, no Ollama. Every class gets TDD coverage via Red-Green-Refactor.

- `SubBlockMatcher` — substring variants, whitespace normalization, unicode normalization.
- `OffsetIndex` — binary search correctness on hand-built indexes.
- `TextConcatenator` — output text and offset index alignment on fake `TextBlock` lists.
- `SpanResolver` — feed synthetic `OffsetIndex` + `RawExtraction`, assert `ExtractedField` shapes including multi-block spans, page-boundary spans, ungrounded values, and sub-block fallback chains.
- `StructuredOutputValidator` — feed malformed strings (markdown fences, prose wrappers, invalid JSON, schema-invalid JSON), assert cleanup and retry behavior with a mock regeneration callable.
- `SkillYamlSchema` — valid and invalid YAMLs, assert pydantic validation messages.
- `SkillLoader` / `SkillManifest` — tmp directory of fake skill YAMLs, assert loading, `latest` resolution, and startup-validation failure modes.
- `PdfAnnotator` — real PyMuPDF against a tiny fixture PDF, assert annotations land on the expected pages.
- `ExtractionService` — stub all dependencies, assert pipeline short-circuiting for each `OutputMode`.

### 9.2 Integration (`tests/integration/features/extraction/`)

- `DoclingDocumentParser` against a small corpus of fixture PDFs (native digital + scanned), asserts reasonable `ParsedDocument`s.
- `ExtractionService` end-to-end via `httpx.AsyncClient` against the FastAPI app with a **stub `IntelligenceProvider`** installed via `Depends()` override returning canned `GenerationResult`s. Covers the full router → service → parsing → annotation chain without touching a real LLM.
- Multipart response parsing round-trip for `OutputMode.BOTH`.

### 9.3 Contract

Schemathesis against the OpenAPI spec for `POST /api/v1/extract`.

### 9.4 E2E (optional, slow)

- One golden-path test against a running Ollama + real Gemma 4 model with a single invoice PDF, asserting extraction produces the expected fields. Marked `slow`, excluded from the default `task check` run, runnable via `task test:slow`.

The rest of the test suite never touches a real LLM — determinism is required for CI.

## 10. Architectural Rules (import-linter)

Added to `apps/backend/architecture/import-linter-contracts.ini`:

- **Feature independence:** `features.extraction` cannot import from any other feature.
- **Intra-feature layer DAG:** within `features.extraction`, imports follow the dependency graph of the pipeline:
  - `router` may import from `service` and `schemas` only.
  - `service` may import from every subpackage below.
  - `extraction` may import from `intelligence`, `skills`, and `parsing` (for `ParsedDocument` / `TextBlock` passed through to LangExtract).
  - `coordinates` may import from `parsing` (for `TextBlock`, `ParsedDocument`) and `extraction` (for `RawExtraction`).
  - `annotation` may import from `schemas` (for `ExtractedField` and `BoundingBoxRef`).
  - `parsing`, `intelligence`, and `skills` may not import from any other subpackage — they are leaves.
  - `schemas` may not import from any other subpackage.
- **Third-party containment:**
  - `docling` may only be imported inside `parsing/docling_document_parser.py`.
  - `pymupdf` (`fitz`) may only be imported inside `annotation/pdf_annotator.py` and `parsing/docling_document_parser.py` (if needed for password-detection preflight).
  - `langextract` may only be imported inside `extraction/extraction_engine.py` and the custom LangExtract community provider plugin file.
  - The Ollama HTTP client may only be imported inside `intelligence/ollama_gemma_provider.py`.

Contracts are enforced by `task lint` (already wired).

## 11. Template Cleanup

Performed in the same PR as the extraction feature build-out:

**Removed:**

- `apps/backend/app/features/widget/` — entire directory.
- `apps/backend/app/core/database.py`.
- `apps/backend/app/shared/base_repository.py`.
- `apps/backend/app/shared/base_model.py`.
- `apps/backend/app/shared/base_service.py` if its interface is DB-coupled; otherwise retained as a plain base class.
- `apps/backend/alembic/`, `alembic.ini`.
- `apps/backend/app/types/money.py`, `currency.py`.
- `apps/backend/app/schemas/page.py`.
- `apps/frontend/` — entire directory.
- `packages/api-client/` — entire directory.
- `infra/docker/frontend.*`.
- CRUD-specific error codes in `errors.yaml` (`WIDGET_NOT_FOUND`, etc.).

**Modified:**

- `packages/error-contracts/errors.yaml` — CRUD codes removed, extraction codes added, `task errors:generate` re-run.
- `infra/compose/docker-compose.yml` — postgres service removed, frontend service removed, note added that Ollama runs on the host.
- `apps/backend/pyproject.toml` — removed `sqlalchemy`, `alembic`, `asyncpg`; added `docling`, `langextract`, `pymupdf`, `jsonschema`, `httpx`, `pyyaml`.
- `apps/backend/architecture/import-linter-contracts.ini` — widget contracts removed, extraction contracts added.
- `Taskfile.yml` — DB-related tasks removed; `task skills:validate` added (standalone `SkillManifest` validation).
- `docs/architecture.md` — rewritten for extraction service.
- `CLAUDE.md` — DB-specific forbidden patterns removed, extraction-specific ones added (e.g. "Never bypass `StructuredOutputValidator`", "Never hardcode an Ollama model tag in source").

**Preserved unchanged:**

- Dependabot configuration and auto-merge workflow.
- CI workflow, pre-commit config, ruff, pyright strict.
- Task runner structure and `task check` contract.
- The error-contracts codegen flow.
- The four-level testing discipline (minus the DB-integration flavor).

## 12. Open Questions Deferred to Implementation

These do not affect the architectural shape and can be resolved during implementation without revisiting this design:

- Exact Ollama tag for the smallest Gemma 4 variant at implementation time — config value, not code.
- Whether `base_service.py` survives the template cleanup — decided by inspection during implementation.
- Whether `host.docker.internal` is the right default `ollama_base_url` for Linux dev hosts — may default to `http://localhost:11434` with documentation instead.
- Exact correction prompt template wording for retries — tuned empirically.
- Whether parser warnings (degraded OCR, etc.) are surfaced via `ExtractionMetadata.parser_warnings` or as a separate logging channel only.

## 14. Quality Attributes

Non-functional targets and hard limits for v1. These are operational goals, not architectural gates. Measurements assume the service running on a modern developer machine (Apple Silicon or equivalent x86-64 workstation) with Ollama serving the smallest Gemma 4 variant locally.

### 14.1 Hard limits (enforced programmatically)

- **Max PDF file size:** 50 MB. Above this, the router rejects the upload with `PDF_TOO_LARGE` (413) before any parsing work is allocated. Configurable via `Settings.max_pdf_bytes`.
- **Max PDF page count:** 200 pages. Above this, `DoclingDocumentParser` raises `PDF_TOO_MANY_PAGES` (413) as soon as the page count is known, before OCR or layout analysis runs. Configurable via `Settings.max_pdf_pages`.
- **Hard end-to-end request timeout:** 180 s. Enforced at the service level via `asyncio.timeout()` and configured by `Settings.extraction_timeout_seconds`. Exceeding the budget returns `INTELLIGENCE_TIMEOUT` (504). This ceiling must cover Docling parsing, LangExtract orchestration (including the 4-attempt validator retry budget), coordinate matching, and annotation combined.
- **Max concurrent in-flight requests:** 1. Uvicorn runs with `workers=1` by default. A second concurrent request blocks on FastAPI's native event-loop scheduling until the in-flight request completes. This is a deliberate v1 simplification — the caller is a single developer machine, Ollama is single-tenant locally, and adding real concurrency primitives (per-request semaphores, OCR parallelism, LangExtract batching) is out of scope for v1. Documented in the runbook.

### 14.2 Latency targets (operational goals, not enforced)

Two profiles, reflecting the dominant cost of OCR:

- **Native digital PDF, ~10 pages, typical invoice-style skill:** `p50 ≤ 20 s`, `p95 ≤ 45 s`. Dominated by LangExtract multi-pass over Gemma 4 (3–8 chunks at 2–5 s per chunk on a modern developer machine).
- **Scanned PDF, ~10 pages, OCR engaged:** `p50 ≤ 60 s`, `p95 ≤ 120 s`. Dominated by Docling's OCR pipeline (20–60 s for 10 pages depending on engine), with LangExtract cost on top.

These targets are not asserted by the test suite. They are a sanity baseline for spot-checks during development and a yardstick for detecting regressions in future iterations. If the numbers turn out to be wrong in practice, this section is updated — the architecture is not.

### 14.3 Memory footprint targets (operational, not enforced)

- **Idle service process (warm, no requests in flight):** `≤ 1.5 GB` RSS. Dominated by Docling's model imports and LangExtract's dependency tree. Documented in the runbook so operators know what to size the container for.
- **Per in-flight request, additional:** `≤ 1 GB` RSS on top of idle. Dominated by Docling's intermediate document representation for large PDFs, the concatenated text buffer, and PyMuPDF's PDF page cache during annotation.
- **Total memory ceiling for a single request:** `≤ 2.5 GB`. If the service consistently exceeds this on representative workloads, it is a regression bug to investigate, not an expected operating condition.

### 14.4 Cold start

- **Container boot to first request accepted:** `≤ 10 s`. Dominated by Docling imports, Ollama connection probe, and `SkillManifest` startup validation. The validation cost scales linearly with the number of skills in `skills_dir` but is O(milliseconds) per skill, so a realistic v1 skill corpus adds negligible cost.

### 14.5 Annotation overhead

- **`PdfAnnotator` for a ~10-page PDF with ~20 highlighted fields:** `≤ 2 s` additional latency compared to the `JSON_ONLY` pipeline. PyMuPDF annotation is fast relative to extraction — this is an operational expectation, not a performance budget that shapes the design.

### 14.6 Explicit non-requirements

Stated explicitly to close the door on scope creep:

- **No high-availability target.** The service is stateless and restartable, but v1 does not guarantee any uptime SLO. If the process dies, a single request in flight is lost.
- **No horizontal scalability.** The architecture does not preclude running multiple instances behind a load balancer, but the service is not tested for it and there is no shared state to coordinate.
- **No authentication, rate limiting, or quota enforcement.** Callers are trusted. Deploying this service exposed to untrusted networks is out of scope.
- **No streaming responses.** Full responses are buffered and returned atomically.
- **No observability beyond structlog.** Structured logs go to stdout. No Prometheus metrics, no distributed tracing, no APM integration. These are future-enhancement territory.

## 15. Out of Scope for v1 (Future Enhancements)

- Per-skill `allow_inference: bool` gating (currently ungrounded values are always returned with a flag; skills can't reject them at authoring time).
- Streaming responses for very large PDFs.
- LangExtract HTML visualization passthrough.
- Skill hot-reload without container restart.
- A second `IntelligenceProvider` implementation (Claude, GPT-4). The protocol is designed to accommodate this, but only the Gemma 4 provider ships in v1.
- Caller-facing extraction confidence scores beyond the grounded/source flags.
