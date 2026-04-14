---
type: epic
id: PDFX-E004
parent: PDFX
title: Intelligence & Extraction Orchestration
status: fully-detailed
priority: 40
dependencies: [PDFX-E002]
---

## Short description

Integrate LangExtract with a locally running Gemma 4 model through a custom `OllamaGemmaProvider` that satisfies both the internal `IntelligenceProvider` protocol and LangExtract's community provider plugin contract, and harden the raw model output with a provider-agnostic `StructuredOutputValidator` that cleans, parses, validates against the skill's JSONSchema, and retries up to 4 total attempts with correction prompts.

## Description

This Epic owns two subpackages: `features/extraction/intelligence/` (the LLM provider abstraction and Ollama-backed implementation) and `features/extraction/extraction/` (the LangExtract orchestration wrapper). It defines the `IntelligenceProvider` `typing.Protocol` with one method (`async generate(prompt, output_schema) -> GenerationResult`), a `GenerationResult` dataclass carrying the parsed `data`, the number of `attempts` taken, and the final `raw_output`, and the `OllamaGemmaProvider` concrete class that simultaneously satisfies the internal protocol and LangExtract's community provider plugin interface. The provider holds one Ollama HTTP client configured by `Settings.ollama_*`; LangExtract invokes it once per chunk in its multi-pass orchestration. Every invocation routes through `StructuredOutputValidator`, a provider-agnostic utility that strips markdown code fences, extracts the first JSON object from prose, parses via `json.loads`, validates via `jsonschema.validate` against the expected schema, and retries on failure — up to 3 additional attempts (4 total) with a correction prompt built by `CorrectionPromptBuilder` that includes the malformed output and a reminder of the schema. After total failure, `STRUCTURED_OUTPUT_FAILED` (502) is raised; after Ollama unreachability or connect timeout, `INTELLIGENCE_UNAVAILABLE` (503) is raised. The `extraction/` subpackage contains `ExtractionEngine`, a thin wrapper that constructs LangExtract's function call parameters from a `Skill` (prompt, examples, output schema) and invokes LangExtract with the injected provider, returning a `list[RawExtraction]` — each with field name, value, character offsets into the concatenated text, and an `grounded` flag from LangExtract's source-grounding behavior.

## Rationale

This Epic exists because the service is fundamentally an LLM-driven extraction pipeline: everything else is plumbing around it. The decision to wrap LangExtract rather than LangExtract itself being wrapped comes from the design spec (Section 3.1) — LangExtract's chunking, multi-pass, and source-grounding logic is too valuable to reimplement, but its default Gemini integration does not apply when running locally against Gemma 4 via Ollama. The provider-agnostic `StructuredOutputValidator` compensates for Gemma 4's lack of native controlled generation by cleaning and retrying at the provider contract level, so the service behaves deterministically against any model that can produce text. Combining the `intelligence/` and `extraction/` subpackages into one Epic reflects that LangExtract *is* the orchestrator that calls the provider — splitting them would create a meaningless seam where LangExtract lives in one Epic and its provider in another. It traces to project success criteria **"structured output success rate ≥ 90% per field"** (validator retry loop), **"native digital PDF extraction latency"** and **"scanned PDF extraction latency"** (the dominant cost — Gemma 4 chunk inference), and **"API stability — every declared field present"** (partial extraction support via per-field status).

## Boundary

**In scope:** the entire `features/extraction/intelligence/` subpackage (`intelligence_provider.py`, `ollama_gemma_provider.py`, `structured_output_validator.py`, `generation_result.py`, `correction_prompt_builder.py`); the `features/extraction/extraction/` subpackage (`extraction_engine.py`, `raw_extraction.py`); LangExtract community provider plugin registration; the Ollama HTTP client and model invocation; the 4-attempt retry loop; `STRUCTURED_OUTPUT_FAILED` and `INTELLIGENCE_UNAVAILABLE` error codes added to `errors.yaml`; `import-linter` contracts containing `langextract` imports to `extraction_engine.py` and the plugin registration file, and containing Ollama client imports to `ollama_gemma_provider.py`; unit tests against a mock `IntelligenceProvider` returning canned results; unit tests for the validator against a library of malformed raw outputs.

**Out of scope:** coordinate matching or span resolution (that's PDFX-E005 — this Epic hands off `list[RawExtraction]` and the concatenated text's offset references are the next Epic's responsibility); any Docling invocation (that's PDFX-E003, which this Epic depends on via PDFX-E002 indirectly); the 180-second end-to-end timeout enforcement (that's PDFX-E006, enforced at the `ExtractionService` level); the `/ready` Ollama probe (that's PDFX-E007, which consumes this Epic's provider health check); any E2E test against a real Ollama + real Gemma 4 model (that's the optional-slow test suite in PDFX-E007).

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- **OQ-004** — Exact correction prompt template wording. Needs empirical tuning against Gemma 4's actual failure modes during integration test authoring. Start with a minimal template (reiterate schema, show malformed output, ask for correction) and iterate.
- Whether the `StructuredOutputValidator` retry loop should apply per-field individually or per-chunk atomically. LangExtract emits extractions per chunk; a chunk containing 5 fields either succeeds wholesale or re-runs wholesale. Default: retry at the chunk level (matching LangExtract's natural granularity); per-field partial success is reported downstream by `SpanResolver` / `ExtractedField.status` after the chunk retries are exhausted.
- Whether `CorrectionPromptBuilder` should be a separate class or a module-level function. Default: a class per CLAUDE.md's one-class-per-file rule, even though it's a thin dataless transformer.
- How to detect "Ollama unreachable" vs "Ollama slow but reachable" vs "Gemma model unavailable" — do these all map to `INTELLIGENCE_UNAVAILABLE`, or should there be finer-grained error codes? Default: all map to `INTELLIGENCE_UNAVAILABLE` (503); log message distinguishes them for operators.
