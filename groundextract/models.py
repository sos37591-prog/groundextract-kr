"""Core data types for the GroundExtract-KR verification gate.

These are plain ``dataclass`` types (no external runtime deps) so the
deterministic pipeline can run without pydantic installed. They are the
contract between the extractor (LLM/OCR), the grounding matcher, the rule
engine and the gate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MatchKind(str, Enum):
    """How a value was found (or not) inside the source text."""

    EXACT = "exact"          # verbatim substring match
    PARTIAL_NUMERIC = "partial_numeric"  # same number, different formatting
    FUZZY = "fuzzy"          # close but not exact (near threshold)
    NONE = "none"            # no acceptable grounding -> hallucination


class Verdict(str, Enum):
    """Final decision for a field after grounding + rules."""

    VERIFIED = "verified"    # grounded AND all rules passed
    DISCARDED = "discarded"  # ungrounded OR a rule was violated -> confidence 0


@dataclass
class BBox:
    """Bounding box of the grounding span on the page (viewer highlight)."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class ExtractedValue:
    """A single value the extractor claims to have read from the document.

    ``raw`` is the string as emitted by the model. ``number`` is the parsed
    numeric value when the field is numeric (used by the rule engine).
    """

    field: str
    raw: str
    number: float | None = None
    grounding_quote: str | None = None  # the verbatim span the model cites
    page: int | None = None
    bbox: BBox | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Check:
    """Result of one verification step (grounding or an arithmetic rule)."""

    name: str            # e.g. "grounding", "vat_equals_supply_x_10pct"
    passed: bool
    detail: str = ""     # human-readable explanation (for the tooltip)
    kind: MatchKind | None = None  # set for grounding checks

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.kind is not None:
            d["kind"] = self.kind.value
        return d


@dataclass
class VerifiedField:
    """The gate's output for one field: value + evidence + verdict.

    This is what downstream consumers, the MCP tool and the bbox viewer read.
    A ``DISCARDED`` field always has ``confidence == 0``.
    """

    field: str
    value: ExtractedValue
    checks: list[Check] = field(default_factory=list)
    verdict: Verdict = Verdict.DISCARDED
    confidence: float = 0.0

    @property
    def grounded(self) -> bool:
        return any(c.name == "grounding" and c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value.to_dict(),
            "checks": [c.to_dict() for c in self.checks],
            "verdict": self.verdict.value,
            "confidence": self.confidence,
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)
