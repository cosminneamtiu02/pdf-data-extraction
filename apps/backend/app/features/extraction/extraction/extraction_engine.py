"""ExtractionEngine — thin async wrapper around `langextract.extract`.

Within the `features/extraction/` subtree, the only files permitted to
import `langextract` are this module, the adjacent
`_validating_langextract_adapter.py` (which hosts the
`_ValidatingLangExtractAdapter` class split out for Sacred Rule #1,
issue #228), and
`app/features/extraction/intelligence/ollama_gemma_provider.py` (the
registered LangExtract community plugin from PDFX-E004-F002). The
containment rule is enforced mechanically by the AST-scan test
`tests/unit/features/extraction/extraction/test_no_third_party_imports.py`
and by the C5 `import-linter` contract in
`apps/backend/architecture/import-linter-contracts.ini` (PDFX-E007-F004).

The engine takes a `Skill`, a pre-concatenated document text, and an
`IntelligenceProvider`, and returns a list of `RawExtraction` — exactly
one per field declared in `skill.output_schema.properties`, in declared
order. Fields LangExtract reported but the skill did not declare are
dropped (hallucinations never leak downstream). Fields the skill declared
but LangExtract did not return are still emitted as placeholder
`RawExtraction(value=None, grounded=False)` rows so every declared field
is present in the output list — matching the project's "every declared
field always present" API-stability criterion. Duplicate field names
within LangExtract's output are deduped first-wins.

**StructuredOutputValidator routing.** LangExtract's orchestration calls
`model.infer(...)` directly, which would bypass the project's validator /
retry path (the fence-stripping + JSON-parse-with-retry loop that gives the
service its structured-output success rate). To keep that invariant intact,
the engine wraps the caller-supplied `IntelligenceProvider` in the
`_ValidatingLangExtractAdapter(BaseLanguageModel)` class defined in the
adjacent module. The adapter's `infer` runs the entire batch under ONE
`asyncio.run_coroutine_threadsafe` bridge (so every prompt shares the main
event loop that holds loop-bound httpx connection pools) and routes each
prompt through `provider.generate(prompt, wrapper_schema)` — which
exercises `StructuredOutputValidator` — then yields the re-serialized
cleaned JSON text for LangExtract's resolver to parse. The wrapper schema
is not `skill.output_schema` (which describes extraction CONTENT and would
always fail against LangExtract's wrapper format) but the LangExtract
envelope shape: `{"type": "object", "required": ["extractions"],
"properties": {"extractions": {"type": "array"}}}`. That validates the
raw model text enough to retry on missing-wrapper failures without leaking
into field-level semantics.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import langextract
from langextract.core.data import AnnotatedDocument, ExampleData, Extraction

from app.core.config import Settings
from app.features.extraction.extraction._validating_langextract_adapter import (
    _ValidatingLangExtractAdapter,  # pyright: ignore[reportPrivateUsage]
    # ^ The adapter and this module are co-owners of the LangExtract
    # orchestration boundary; the leading underscore signals intra-
    # subpackage-private, not module-private, so crossing this module
    # boundary is by design (issue #228 Sacred Rule #1 split).
)
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.skills.deep_freeze import thaw

if TYPE_CHECKING:
    from langextract.core.base_model import BaseLanguageModel
    from langextract.core.data import CharInterval

    from app.features.extraction.intelligence.intelligence_provider import IntelligenceProvider
    from app.features.extraction.skills.skill import Skill
    from app.features.extraction.skills.skill_example import SkillExample


class ExtractionEngine:
    """Constructs LangExtract call parameters from a Skill and invokes it.

    The engine's only configuration is the Ollama timeout budget that bounds
    the `_ValidatingLangExtractAdapter.infer` blocking `future.result(...)`
    per prompt (issue #152). When no `settings` argument is passed the
    engine lazily materializes a `Settings()` from environment variables,
    so callers that instantiated the engine with no argument before the
    fix still work unchanged.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def _ollama_timeout_seconds(self) -> float:
        # Materialize Settings lazily so `ExtractionEngine()` call sites
        # that predate the fix keep working. Pydantic-settings reads from
        # env variables, matching the rest of the service.
        settings = self._settings
        if settings is None:
            settings = Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
            self._settings = settings
        return settings.ollama_timeout_seconds

    async def extract(
        self,
        concatenated_text: str,
        skill: Skill,
        provider: IntelligenceProvider,
    ) -> list[RawExtraction]:
        """Run LangExtract against `concatenated_text` using `skill` + `provider`.

        Returns one `RawExtraction` per distinct field name declared in
        `skill.output_schema`. Empty input text short-circuits LangExtract
        but still emits a placeholder row per declared field so the
        "every declared field always present" invariant (CLAUDE.md:104)
        holds on the empty-text path too.
        """
        declared_fields = declared_field_names(skill)
        if not declared_fields:
            # No declared fields means no API contract to honor — short-
            # circuit rather than inviting hallucinations through. A skill
            # with zero output fields is a skill-authoring error caught
            # upstream by `SkillYamlSchema` validation, but the engine
            # stays strict here as defense in depth.
            return []

        if not concatenated_text:
            # No prompt is worth sending for empty text, but downstream
            # assembly still needs one row per declared field. Route
            # through the same placeholder path `_to_raw_extractions`
            # uses for declared-but-missing fields so the shape is
            # identical to the normal "LangExtract returned nothing"
            # branch.
            return self._to_raw_extractions(
                AnnotatedDocument(extractions=[], text=""),
                declared_fields,
            )

        examples = self._build_examples(skill.examples)
        # Capture the running loop so the adapter, once LangExtract calls
        # it from the worker thread, can schedule `provider.generate`
        # coroutines back onto THIS loop via `run_coroutine_threadsafe`.
        # That keeps the shared httpx client pool on its original loop.
        main_loop = asyncio.get_running_loop()
        adapter = _ValidatingLangExtractAdapter(
            provider,
            main_loop,
            timeout_seconds=self._ollama_timeout_seconds(),
        )

        result = await asyncio.to_thread(
            self._invoke_langextract,
            concatenated_text,
            skill.prompt,
            examples,
            adapter,
        )

        # Read the validator's retry count out of the adapter side-channel
        # (issue #135). If the adapter observed at least one GenerationResult,
        # that count stamps every declared field; otherwise we fall back to
        # the legacy default of 1.
        attempts = max(adapter.max_observed_attempts, 1)
        return self._to_raw_extractions(result, declared_fields, attempts=attempts)

    @staticmethod
    def _invoke_langextract(
        text: str,
        prompt: str,
        examples: list[ExampleData],
        model: BaseLanguageModel,
    ) -> AnnotatedDocument:
        result: Any = langextract.extract(
            text_or_documents=text,
            prompt_description=prompt,
            examples=examples,
            model=model,
            max_workers=1,
            batch_length=1,
            show_progress=False,
            fetch_urls=False,
        )
        # LangExtract returns `AnnotatedDocument | list[AnnotatedDocument]`;
        # single-string input always produces a single `AnnotatedDocument`.
        if isinstance(result, list):
            if not result:
                return AnnotatedDocument(extractions=[], text=text)
            return cast("AnnotatedDocument", result[0])
        return cast("AnnotatedDocument", result)

    @staticmethod
    def _build_examples(skill_examples: tuple[SkillExample, ...]) -> list[ExampleData]:
        built: list[ExampleData] = []
        for example in skill_examples:
            extractions = [
                Extraction(
                    extraction_class=field_name,
                    # Thaw frozen values (MappingProxyType -> dict, tuple ->
                    # list) before str() so prompt serialization matches the
                    # plain-dict/list representation skill authors expect.
                    extraction_text=str(thaw(value)),
                )
                for field_name, value in example.output.items()
            ]
            built.append(ExampleData(text=example.input, extractions=extractions))
        return built

    @staticmethod
    def _to_raw_extractions(
        result: AnnotatedDocument,
        declared_fields: tuple[str, ...],
        *,
        attempts: int = 1,
    ) -> list[RawExtraction]:
        """Map LangExtract's native `Extraction` list to `RawExtraction`.

        Invariants enforced here (from PDFX-E004-F003 AC + the project's
        "every declared field always present" API-stability rule):

        - Output order matches `declared_fields` exactly.
        - Fields NOT declared in `skill.output_schema.properties` are
          dropped (hallucinated / extra fields never leak downstream).
        - Fields declared but MISSING from LangExtract's output appear as
          placeholder `RawExtraction(value=None, grounded=False)` rows so
          downstream assembly can always look them up by field name.
        - Duplicate field names within LangExtract's output are deduped
          first-wins.
        - Every output row (real value or placeholder) carries the same
          ``attempts`` value — the caller (``extract``) sources it from
          the adapter's side-channel so ``ExtractionMetadata.attempts_per_field``
          reflects the validator's retry count (issue #135). Short-circuit
          paths (empty text, zero declared fields) default to 1.
        """
        extractions = result.extractions or []
        # Precompute a set for O(1) membership checks during the filter
        # loop; the `declared_fields` tuple is still the source of truth
        # for output ordering further down.
        declared_set = frozenset(declared_fields)
        by_field: dict[str, Extraction] = {}
        for extraction in extractions:
            field_name = extraction.extraction_class
            if field_name not in declared_set:
                continue
            if field_name in by_field:
                continue
            by_field[field_name] = extraction

        output: list[RawExtraction] = []
        for field_name in declared_fields:
            extraction = by_field.get(field_name)
            if extraction is None:
                # Declared but missing — emit a placeholder so callers can
                # still look this field up by name and see status=missing.
                output.append(
                    RawExtraction(
                        field_name=field_name,
                        value=None,
                        char_offset_start=None,
                        char_offset_end=None,
                        grounded=False,
                        attempts=attempts,
                    ),
                )
                continue

            start, end = _sanitize_char_interval(extraction.char_interval)
            grounded = start is not None and end is not None

            output.append(
                RawExtraction(
                    field_name=field_name,
                    value=extraction.extraction_text,
                    char_offset_start=start,
                    char_offset_end=end,
                    grounded=grounded,
                    attempts=attempts,
                ),
            )
        return output


def _sanitize_char_interval(
    interval: CharInterval | None,
) -> tuple[int | None, int | None]:
    """Normalize a LangExtract `CharInterval` into sane offsets.

    Returns `(start, end)` suitable for `RawExtraction`:
      - `(None, None)` when the interval is absent OR structurally invalid
        (missing endpoints, negative, or `start >= end`).
      - `(start, end)` when both endpoints are present and form a valid
        half-open range with non-negative integers.

    Malformed intervals from LangExtract are coerced to ungrounded rather
    than raising, so a single bad span does not nuke the whole extraction.
    Downstream coordinate resolution then simply treats that field as
    inferred/ungrounded.
    """
    if interval is None:
        return None, None
    start = interval.start_pos
    end = interval.end_pos
    if start is None or end is None:
        return None, None
    if start < 0 or end < 0 or start >= end:
        return None, None
    return start, end


def declared_field_names(skill: Skill) -> tuple[str, ...]:
    """Names of fields declared in the skill's JSONSchema `properties`.

    Returns an empty tuple if the skill schema has no `properties` block —
    which `extract` uses as a "strict-empty" signal to return no results
    rather than letting LangExtract output pass through unfiltered. Order
    of insertion into the JSONSchema is preserved (Python dicts are
    ordered) and becomes the output order of `RawExtraction`.
    """
    # `Skill.from_schema` wraps nested schema maps in `MappingProxyType`, so
    # `properties` is typically not a plain `dict` at runtime. Accept any
    # `Mapping` so loader-produced skills filter correctly.
    properties_obj: Any = skill.output_schema.get("properties")
    if not isinstance(properties_obj, Mapping):
        return ()
    return tuple(str(name) for name in cast("Mapping[Any, Any]", properties_obj))
