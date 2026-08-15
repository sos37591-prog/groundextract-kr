"""Extractor interface (protocol only).

The real extractor uses an LLM with function-calling to read numbers out of a
normalized document and cite the verbatim span each number came from. That is
intentionally NOT implemented here so the deterministic gate can be tested with
no API keys and no network.

Wire a real backend by implementing ``Extractor.extract`` — see the TODOs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import ExtractedValue


@runtime_checkable
class Extractor(Protocol):
    """Turns a normalized document (text + optional layout) into values.

    Implementations MUST populate ``ExtractedValue.grounding_quote`` with the
    verbatim source span so the gate can verify it. ``number`` should be the
    parsed numeric value for numeric fields.
    """

    def extract(self, doc_text: str, *, doc_type: str) -> list[ExtractedValue]:
        ...


# TODO(real-llm): OpenWeightExtractor — Ollama/vLLM serving Qwen2.5/Llama with
#   function-calling. Prompt: "for each field return {value, quote}" where
#   `quote` must be copied verbatim from the input (enforced by the gate).
# TODO(ocr): DoclingAdapter — PDF -> text/tables/bbox before extraction; feeds
#   `page`/`bbox` onto each ExtractedValue for the highlight viewer.
