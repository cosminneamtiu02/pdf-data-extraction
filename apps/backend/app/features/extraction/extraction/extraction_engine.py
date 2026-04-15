"""ExtractionEngine — thin async wrapper around `langextract.extract`.

This is the only file in the extraction feature permitted to import
`langextract` (plus, once PDFX-E004-F002 lands on main, the sibling
`ollama_gemma_provider.py` that registers the plugin). The containment rule
is enforced mechanically by the AST-scan test
`tests/unit/features/extraction/extraction/test_no_third_party_imports.py`
until the full `import-linter` contract arrives in PDFX-E007-F004.

The engine takes a `Skill`, a pre-concatenated document text, and an
`IntelligenceProvider` instance (in practice the dual-interface
`OllamaGemmaProvider`, which is both an `IntelligenceProvider` and a
LangExtract `BaseLanguageModel`), and returns a list of `RawExtraction`
objects — one per declared field, deduped to the first occurrence when
LangExtract reports the same field across multiple chunks.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import langextract
from langextract.core.data import AnnotatedDocument, ExampleData, Extraction

from app.features.extraction.extraction.raw_extraction import RawExtraction

if TYPE_CHECKING:
    from langextract.core.base_model import BaseLanguageModel

    from app.features.extraction.intelligence.intelligence_provider import IntelligenceProvider
    from app.features.extraction.skills.skill import Skill
    from app.features.extraction.skills.skill_example import SkillExample


class ExtractionEngine:
    """Constructs LangExtract call parameters from a Skill and invokes it."""

    async def extract(
        self,
        concatenated_text: str,
        skill: Skill,
        provider: IntelligenceProvider,
    ) -> list[RawExtraction]:
        """Run LangExtract against `concatenated_text` using `skill` + `provider`.

        Returns one `RawExtraction` per distinct field name reported by
        LangExtract. An empty input text short-circuits to an empty result
        without touching the provider.
        """
        if not concatenated_text:
            return []

        examples = self._build_examples(skill.examples)
        # The `provider` parameter is typed as the internal Protocol because
        # that is what the rest of the codebase passes around, but LangExtract
        # requires a `BaseLanguageModel` subclass. `OllamaGemmaProvider` is
        # both — the dual-interface design of PDFX-E004-F002. At this seam we
        # cast once so pyright sees the LangExtract-facing shape.
        model = cast("BaseLanguageModel", provider)

        result = await asyncio.to_thread(
            self._invoke_langextract,
            concatenated_text,
            skill.prompt,
            examples,
            model,
        )

        return self._to_raw_extractions(result)

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
                    extraction_text=str(value),
                )
                for field_name, value in example.output.items()
            ]
            built.append(ExampleData(text=example.input, extractions=extractions))
        return built

    @staticmethod
    def _to_raw_extractions(result: AnnotatedDocument) -> list[RawExtraction]:
        extractions = result.extractions or []
        seen: set[str] = set()
        output: list[RawExtraction] = []
        for extraction in extractions:
            field_name = extraction.extraction_class
            if field_name in seen:
                continue
            seen.add(field_name)

            interval = extraction.char_interval
            start = interval.start_pos if interval is not None else None
            end = interval.end_pos if interval is not None else None
            grounded = start is not None and end is not None

            output.append(
                RawExtraction(
                    field_name=field_name,
                    value=extraction.extraction_text,
                    char_offset_start=start,
                    char_offset_end=end,
                    grounded=grounded,
                    attempts=1,
                ),
            )
        return output
