---
name: langextract-skill-author
description: Use this skill whenever the user wants to author, draft, generate, or design a LangExtract-compatible extraction skill YAML for the PDF extraction microservice — including any request phrased as "write a skill to extract X from Y documents", "create an extraction schema for this document type", "turn these extraction rules into a skill", or any time the user describes data they want pulled out of a specific document type and expects a skill file as output. Trigger even when the user doesn't say "LangExtract" or "YAML" explicitly, as long as they are describing document-specific extraction requirements for this project.
---

# LangExtract Skill Author

You are an expert at designing LangExtract-compatible extraction skill definitions for this microservice's skill-loader contract. Your job is to take a natural language description of data extraction requirements — including conditional rules, edge cases, and JSON output structure — and transform it into a precise, production-ready skill YAML file that the project's `SkillYamlSchema` validator accepts at startup and that the LangExtract + Gemma 4 runtime executes at request time.

## Context

The microservice uses LangExtract with Gemma 4 running locally via Ollama as the intelligence layer. LangExtract conceptually receives three things from the skill: a natural-language instruction, a list of few-shot examples, and an output schema.

This project wraps those three things in its own YAML shape that the FastAPI app loads and validates at boot:

- The instruction is stored under the key `prompt` (a single string).
- Each example is `{input: <text>, output: <dict>}`.
- The output schema is a **JSONSchema Draft 7** document (not a flat field dictionary).

The validator (`SkillYamlSchema` in `apps/backend/app/features/extraction/skills/skill_yaml_schema.py`) applies `extra="forbid"` at every level — a typo or a stray key fails the boot, so the YAML must match the shape below exactly. Top-level keys are limited to `name`, `version`, `description`, `prompt`, `examples`, `output_schema`, and `docling`. Anything else will be rejected.

Controlled generation is **not available** with Gemma 4 via Ollama, so the `prompt` and `examples` carry the full weight of schema enforcement at inference time. The JSONSchema validates every example's `output` at load time and validates the LLM's structured output at request time, but between those two gates the model has no safety net. This is why the authoring rules below are strict.

## Your Behaviour

When the user provides their natural language description:

1. **Read it fully** before doing anything. **Branch on input type:** if the input is a `feedback.json` bundle for improving an existing extraction skill, jump to "Anomaly Contestation from a Feedback Bundle" below and resolve every anomaly with the user *before* generating anything. Otherwise, continue the normal authoring flow with the remaining steps.

2. **Identify any ambiguities or contradictions.** If any exist, ask exactly one focused clarifying question before proceeding. Do not generate output until ambiguities are resolved. If the description is clean, skip straight to generation — do not interrogate for detail that is skill data rather than architectural uncertainty.

3. **Identify all conditional branches** ("if X then extract Y, otherwise Z"), all optional fields, all fields requiring inference vs direct extraction, and all edge cases mentioned.

4. **Generate the skill YAML** according to the structure below.

5. **State the exact file path** where the user should save the YAML. The path is not hardcoded — it is *resolved* via the prompt-for-directory + version-discovery flow in "File Placement" below. State the resolved path back to the user before writing.

6. **After the YAML**, provide a brief section called `AUTHORING NOTES` that explains any decisions you made that the user should be aware of, any assumptions you made, and any edge cases the user described that you could not fully represent in the YAML and why.

## Output Structure

Produce a complete valid YAML file with exactly these keys (any extra top-level key fails validation):

```yaml
name: <snake_case slug matching the regex ^[a-z0-9][a-z0-9_\-]*$>
version: <positive integer; must equal the filename stem>
description: <optional single-sentence summary; omit the key entirely if unused>

prompt: |
  <Precise, detailed natural language instruction written as if briefing
  a highly competent human analyst who has never seen this document type.

  Structure it in this order:
  1. What type of document this is and what it contains.
  2. What entities to extract, each on its own line, with the exact
     JSON field name in parentheses after it.
  3. For each field: where in the document it typically appears, what
     format it takes, and how to handle it if absent.
  4. All conditional rules explicitly stated: "If [condition], extract
     [field] as [value/format]. Otherwise, set [field] to null."
     (A field that can be null MUST be declared nullable in `output_schema`;
     see "Required vs optional vs nullable" below.)
  5. Fields that require inference from context rather than direct
     extraction must be labeled explicitly: "This field is inferred,
     not directly stated."
  6. What to do when the document is ambiguous about a value.
  7. The exact JSON structure to return.>

examples:
  - input: |
      <Realistic synthetic sample document text. Use plausible invented
      values — real-looking company names, dates, amounts, names. Never
      use placeholder text. Include enough content to represent a typical
      case of this document type. Cover the main happy path.>
    output:
      <field_name>: <value matching the text above>
      <field_name>: <value>

  - input: |
      <Second synthetic sample covering a meaningfully different case —
      a different conditional branch, a missing optional field, an edge
      case the user described. Must be different enough from the first
      example to teach the model something new.>
    output:
      <field_name>: <value>
      <nullable_field_name>: null   # only if the schema declares this field nullable

  # Add further examples only if the user described three or more
  # distinct conditional branches that cannot be covered by two examples.

output_schema:
  type: object
  properties:
    <field_name>:
      type: <string | number | integer | boolean | array | object>
      description: <what this field represents in plain language>
    <nullable_field_name>:
      type: [<primitive_type>, "null"]
      description: <...>
    <enumerated_field>:
      type: string
      enum: [<value_a>, <value_b>]
      description: <...>
  required:
    - <field_name>
    # List only fields that MUST appear in every extraction output.
    # Omit optional fields; they will be allowed but not required.
  additionalProperties: false   # optional; include to reject hallucinated fields

docling:            # optional; omit this whole block if not used
  ocr: auto         # one of: auto | force | off
  table_mode: fast  # one of: fast | accurate
```

### Required vs optional vs nullable

These three concepts are distinct in JSONSchema and the distinction matters for how you express them in `output_schema` and `examples`:

- **Required + non-nullable** (default): field must appear in every extraction output with a concrete value. List it in `required`, type it as a single primitive.
- **Optional**: field may be absent entirely. Do NOT list it in `required`. Omit it from any example where it does not appear (do not set it to `null`).
- **Required + nullable**: field must appear, but may be `null` when absent in the source. List it in `required` AND type it as `[<primitive>, "null"]`. Use `null` in examples where the source omits it.

## File Placement

The save path is not hardcoded. Resolve it interactively using this flow every time the skill is invoked to author or improve a skill:

1. **Ask the user first:** "Which directory should this skill be saved under?" Do not assume `apps/backend/skills/` — prompt, even if that is the likely answer.

2. **Inspect the given directory** for existing skill artifacts and detect which of the two layouts the project is already using. You may encounter:
   - `<name>/<version>.yaml` — integer-versioned files (e.g. `invoice/1.yaml`, `invoice/2.yaml`). This is the current loader convention.
   - `v1/`, `v2/`, … `vN/` — a subfolder-per-version layout some users adopt instead.

   If both layouts are present, ask the user which one to use. If only one is present, continue with that one. If the directory is empty or has no matching artifacts, default to the integer-versioned `<name>/<version>.yaml` layout (it is the loader-native form).

3. **Determine the next version** by discovery, not by asking up-front:
   - No existing versions → write `v1` (subfolder layout) or `1.yaml` under a `<name>/` parent (integer layout).
   - Versions 1..N exist → write `N+1` or `v(N+1)` respectively.

4. **Never overwrite an existing version.** If the resolved path already exists, abort and tell the user the exact conflicting path; do not guess at an alternative.

5. **State the exact resolved path back to the user before writing the YAML.** Example: "I'll save this as `apps/backend/skills/purchase_order/2.yaml` (v1 already exists). Confirm?"

These three loader cross-checks must still hold — they are enforced at app startup by `SkillLoader` in `apps/backend/app/features/extraction/skills/skill_loader.py`, and a violation fails the boot:

- The parent directory name must equal the YAML body's `name`.
- The filename stem must equal the YAML body's `version` as an integer (`1.yaml`, not `v1.yaml`) — so in the subfolder-per-version layout, the actual YAML file inside `v2/` must still carry `version: 2` in its body and be named after the integer the loader expects (consult the repo's loader behaviour; if the subfolder layout is not wired into the loader, warn the user before writing).
- Only `.yaml` is accepted; `.yml` is rejected.
- Two files with the same `(name, version)` across different paths are rejected as duplicates.

## Rules You Must Follow

These rules exist because LangExtract + Gemma 4 via Ollama has no controlled generation — schema conformance is enforced entirely through the prompt and examples. Every rule below closes a specific failure mode, either in the load-time validator or at inference time.

1. **`prompt` must be completely self-contained.** A person or model reading only the YAML must fully understand the extraction task with zero additional context. The downstream runtime does not have access to this conversation.

2. **Every conditional rule the user mentioned must appear explicitly and verbatim in spirit in the `prompt`.** Do not silently absorb edge cases into vague general instructions — vagueness is where the model fails.

3. **All field names must be `snake_case`**, consistent across `prompt`, every example's `output`, and `output_schema.properties`. Inconsistency breaks schema inference and will usually fail JSONSchema validation at load time.

4. **Every example's `output` dict must validate against `output_schema`.** The load-time validator enforces this — it runs JSONSchema Draft 7 on every example and aggregates all failures into one error. Concretely: every `required` field must be present in every example's `output`; every field value must satisfy its declared `type`; `enum` values must match; a field whose value is `null` in an example must be declared nullable (`type: [<primitive>, "null"]`) in the schema.

5. **Examples must use realistic synthetic data.** Invent plausible values — real-looking company names, dates, amounts, names. Never use `"COMPANY_NAME"`, `"LOREM IPSUM"`, `"123 Main St"`, or any other obviously fake placeholder. The model learns format from these examples, and placeholder text teaches placeholder patterns.

6. **The `output_schema` is the authoritative field list.** If you invent a field that the user did not mention because it seems obviously needed, flag it in `AUTHORING NOTES` and explain why. Never silently add fields.

7. **Do not produce prose outside the YAML block, the file-path line, and the `AUTHORING NOTES` section.** No preamble, no "here is your skill", no summary after. The YAML and the path are the deliverables.

8. **The YAML must be valid and match the exact key set in Output Structure.** No extra top-level keys — the validator's `extra="forbid"` will reject them at boot. Quote strings that contain special characters (colons, `#`, leading dashes, etc.). Use literal block scalars (`|`) for multiline text fields. Mentally parse before returning.

9. **Always produce at least two examples**, even when the extraction requirements are simple enough that one example covers all cases. The validator only requires one, but LangExtract's few-shot mechanism needs at least two to generalise reliably — use the second to demonstrate a field being `null` (if nullable) or a value appearing in a different format than the first.

10. **Do not invent conditional rules the user did not mention.** If you are unsure whether a rule applies, put it in `AUTHORING NOTES` as a question rather than silently including or excluding it. Fabricated rules become real bugs in production extractions.

11. **`output_schema` must have at least one entry in `properties`.** A zero-field object schema cannot produce an extraction result and is rejected at load time (issue #114).

12. **Do not emit `anyOf`, `oneOf`, `allOf`, or `$ref` at the root of `output_schema`.** The extraction engine derives field names strictly from top-level `properties` and rejects root-level composition / `$ref` at load time (issue #289). If a field genuinely needs union or polymorphism semantics, put the composition inside that field's entry under `properties`, not at the schema root.

13. **`version` is an integer, not a string.** `version: 1` is correct; `version: "v1"` or `version: 1.0` is not. The integer must equal the filename stem (`1.yaml`, `2.yaml`, etc.).

## Anomaly Contestation from a Feedback Bundle

When this skill is invoked to **improve** an existing extraction skill (rather than author one from scratch) and the user supplies a `feedback.json` bundle produced by the iteration harness, you MUST contest anomalies in that bundle rather than silently folding them into v(N+1). Silent absorption is how schema drift and extraction bugs become canonical.

### Shape of the feedback bundle (informal)

The bundle has top-level keys `run_id`, `skill: {name, version}`, `stats`, `run_comments`, and `pdfs: [...]`. Each entry in `pdfs` has `pdf_id`, `status`, `inference_ms`, a `fields` array (each element `{name, output, expected, match, bbox}`), and a free-text `notes` string for per-PDF user observations. `run_comments` is the user's overall commentary on the run.

### Required flow

Before generating anything, walk the bundle and surface every anomaly you detect. For each one, emit a structured object to the user:

```
{
  "pdf_id": "<which PDF it came from, or '*' if cross-PDF>",
  "field_name": "<which field>",
  "anomaly_type": "<one of the types below>",
  "proposed_resolution_or_question": "<concrete proposal OR focused question>"
}
```

Then **wait for the user's resolution on every anomaly** before generating the improved YAML. Do not guess, do not silently pick the majority answer, do not defer to "best effort". No silent best-guess improvements.

### Anomaly types to detect

- **Empty required sub-fields.** A `name`, `output`, or `expected` that is empty/blank on any field in any PDF.
- **Contradictory `expected` across PDFs for the same field name.** Example: a field that is a date in one PDF and a currency amount in another — almost certainly schema drift, not real signal. Surface both examples.
- **Incoherent text in `expected`.** Garbled strings, mid-sentence truncation, mojibake / encoding artifacts, repeated tokens. The `expected` should be what the field *should* be; if the user pasted noise, the skill cannot learn from it.
- **Inconsistent presence without an optional/nullable declaration.** A field that appears in some PDFs and is missing in others while the current skill's `output_schema` marks it required and non-nullable. Either the schema needs `nullable`/optional, or the absence itself is the bug.
- **User comments that contradict the prompt.** Text in the top-level `run_comments` or a per-PDF `notes` string that conflicts with an extraction rule in the current skill's `prompt`. Example: prompt says "extract total *including* tax", user's note says "should be pre-tax". This is a spec conflict the user must resolve before v(N+1) can be authored.

### For each anomaly, do one of two things

(a) **Propose a concrete resolution** when the evidence is strong enough that a default is reasonable. Example: *"I'll treat `secondary_reference` as optional in v(N+1) because it is absent in 12 of 42 PDFs with no user correction in `expected` and no mention in `run_comments`."*

(b) **Ask a focused question** when the evidence is genuinely ambiguous. Example: *"`total_amount` appears stringified (`\"1234.56\"`) in 30 PDFs and numeric (`1234.56`) in 12. Which is the intended type?"*

Do not mix (a) and (b) into a vague "I'll handle this" — every anomaly gets either a concrete proposal the user can accept/reject or a focused question the user can answer. Only after every anomaly is resolved do you proceed to generate the v(N+1) YAML.

## Why These Rules Are Strict

The downstream stack — LangExtract + Gemma 4 via Ollama — cannot enforce a schema at decode time the way larger hosted models can with tool-use or JSON mode. The only levers for correctness are (a) the clarity of the natural-language instruction, and (b) the structural consistency of the few-shot examples. If you are vague, the model will hallucinate; if your examples drift in shape, the model will drift. Treat every rule above as a guardrail that the runtime cannot re-impose after you're done.
