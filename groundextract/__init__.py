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
from .rules import RulePack, default_rules_dir, evaluate_pack, load_rule_pack

__version__ = "0.1.0"


def available_doc_types() -> list[str]:
    """Document types that ship with a bundled rule pack, sorted by name."""
    return sorted(p.stem for p in default_rules_dir().glob("*.yaml"))


def load_pack(doc_type: str) -> RulePack:
    """Load a bundled rule pack by document type, e.g. ``load_pack("tax_invoice")``.

    Prefer this over ``load_rule_pack("rules/<doc_type>.yaml")``: that relative
    path only resolves from a repository checkout, while the bundled packs are
    located through :func:`default_rules_dir`, so this works from any working
    directory and from a ``pip install``ed wheel.

    Raises ``FileNotFoundError`` (listing the available types) when no pack
    ships for ``doc_type``. Use :func:`load_rule_pack` for your own YAML files.
    """
    if not doc_type.replace("_", "").isalnum():  # no path separators / traversal
        raise ValueError(f"invalid doc_type: {doc_type!r}")
    path = default_rules_dir() / f"{doc_type}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"no bundled rule pack for doc_type {doc_type!r}; "
            f"available: {', '.join(available_doc_types()) or '(none)'}"
        )
    return load_rule_pack(path)


__all__ = [
    "BBox",
    "Check",
    "ExtractedValue",
    "MatchKind",
    "Verdict",
    "VerifiedField",
    "RulePack",
    "available_doc_types",
    "default_rules_dir",
    "load_pack",
    "load_rule_pack",
    "evaluate_pack",
    "ground_value",
    "match_value",
    "run_gate",
    "summarize",
    "__version__",
]
