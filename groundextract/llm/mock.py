"""Mock extractor for key-free, deterministic tests and demos.

``MockExtractor`` returns a caller-supplied list of ``ExtractedValue`` verbatim.
It lets the gate/grounding/rule logic be exercised end-to-end without an LLM.
To simulate a numeric hallucination, hand it a value whose ``grounding_quote``
is absent from the document (or wrong). To simulate a rule violation, give it
numbers that break an invariant (e.g. VAT != supply * 10%).
"""

from __future__ import annotations

from ..models import ExtractedValue


class MockExtractor:
    """Implements the ``Extractor`` protocol by replaying canned values."""

    def __init__(self, values: list[ExtractedValue]) -> None:
        self._values = values

    def extract(self, doc_text: str, *, doc_type: str) -> list[ExtractedValue]:
        # Deterministic: ignores the inputs, returns the scripted values.
        return list(self._values)
