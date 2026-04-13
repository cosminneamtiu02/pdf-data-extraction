---
type: epic
id: PDFX-E002
parent: PDFX
title: Skill System Foundation
status: not-started
priority: 20
dependencies: [PDFX-E001]
---

## Short description

Define, load, validate, and register the skill data layer — YAML files under `skills/{name}/{version}.yaml` — as an in-memory startup-built `SkillManifest` that the extraction pipeline can consume by `(name, version)` or `(name, "latest")` without any runtime disk I/O.

## Description

Skills are the per-document-type configuration data layer of the service. Each skill YAML declares `name`, integer `version`, optional `description`, a `prompt` string, a list of few-shot `examples` (each with `input` and `output`), an `output_schema` that is a valid JSONSchema describing the extraction result shape, and an optional `docling:` section for parser config overrides. This Epic owns the entire `skills/` subpackage of the extraction feature: the `Skill` runtime domain object, the `SkillYamlSchema` pydantic validator, the `SkillLoader` that walks the filesystem at startup, and the `SkillManifest` in-memory registry. The Epic also introduces the `skills/` data directory at the repository-relative path configured by `Settings.skills_dir` and adds two new error codes to `errors.yaml`: `SKILL_NOT_FOUND` (runtime, 404) and `SKILL_VALIDATION_FAILED` (startup, non-zero exit). The hybrid-manifest validation model means every YAML is fully parsed and checked at container startup — including verifying that each example's `output` validates against the skill's declared `output_schema` and that the filename integer matches the body `version` field — so that a broken skill kills the boot rather than surfacing at request time.

## Rationale

This Epic exists because the extraction pipeline cannot function without a way to look up a skill by `(name, version)`: every request arrives with a skill reference, and every downstream component (parser, engine, span resolver, annotator) operates against the resolved `Skill` domain object. It is a leaf dependency of the pipeline — nothing in the extraction code path can run without it — so it must exist in concrete form before PDFX-E004 (Intelligence & Extraction) or PDFX-E005 (Coordinate Matching) can be tested end-to-end. It traces to the Project success criterion **"API stability — every field declared in a skill's `output_schema` is always present in the response"**, which is only meaningful if the skill itself is loaded and its `output_schema` is trustworthy; and to **"cold start to `/ready` green ≤ 10 s"** via the startup-time validation pattern (which must complete quickly for a realistic skill corpus).

## Boundary

**In scope:** the entire `features/extraction/skills/` subpackage (`skill.py`, `skill_yaml_schema.py`, `skill_loader.py`, `skill_manifest.py`); the `skills/` data directory path configuration via `Settings.skills_dir`; the two new error codes `SKILL_NOT_FOUND` and `SKILL_VALIDATION_FAILED` added to `errors.yaml` with codegen re-run; the skill-lookup request flow from router to `SkillLoader.load` (via `ExtractionService`) including the `latest` alias resolution; startup-time registration of all discovered skills; the `task skills:validate` standalone Taskfile target for spot-checking skill authoring outside a full container boot.

**Out of scope:** authoring any actual skill YAMLs beyond a minimal fixture for testing (that belongs to the caller / skill author, not the microservice build-out); applying the `docling:` config section to an actual Docling run (that lives in PDFX-E003, which *consumes* `Skill.docling_config`); integrating the `Skill` object into LangExtract's function call parameters (that lives in PDFX-E004); skill hot-reload without restart (explicitly deferred to v2); any persistence of skills to a database (the service has no database).

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- Whether the skill manifest should expose a read-only enumeration of registered `(name, version)` pairs via some diagnostic endpoint for debugging purposes, or whether that's out of scope for v1. Default: out of scope — operators can inspect `skills_dir` directly.
- The exact shape of the `examples[*].input` field in skill YAML — plain string, or a richer structured input type. Default: plain string (what LangExtract expects for its few-shot prompting).
- Whether the `task skills:validate` standalone command can run without fully booting FastAPI (i.e. without needing `Settings` to be fully resolved). Default: yes, it should — it's a dev-loop ergonomic target.
