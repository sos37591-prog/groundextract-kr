"""GroundExtract-KR — value-level truth gate for Korean regulatory documents.

Every extracted number is forced to (1) cite verbatim evidence, (2) satisfy
domain arithmetic invariants; anything ungrounded or rule-violating is
auto-discarded at confidence 0 before it reaches downstream.
"""

from .gate import run_gate, summarize
from .grounding import ground_value, match_value
from .models import (
    BBox,
    Check,
    ExtractedValue,
    MatchKind,
    Verdict,
    VerifiedField,
)
from .rules import RulePack, evaluate_pack, load_rule_pack

__version__ = "0.1.0"

__all__ = [
    "BBox",
    "Check",
    "ExtractedValue",
    "MatchKind",
    "Verdict",
    "VerifiedField",
    "RulePack",
    "load_rule_pack",
    "evaluate_pack",
    "ground_value",
    "match_value",
    "run_gate",
    "summarize",
    "__version__",
]
