"""End-to-end gate tests: happy path, numeric hallucination, rule violation.

These exercise the deterministic core (grounding + rules + auto-discard) with
the key-free MockExtractor. No LLM, no network.
"""

from __future__ import annotations

from pathlib import Path

from groundextract import (
    ExtractedValue,
    MatchKind,
    Verdict,
    load_rule_pack,
    match_value,
    run_gate,
    summarize,
)
from groundextract.llm import MockExtractor
from groundextract.rules import eval_expr

RULES = Path(__file__).resolve().parent.parent / "rules" / "tax_invoice.yaml"

# A realistic tax-invoice text snippet the values must ground against.
DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)


def _pack():
    return load_rule_pack(RULES)


def _by_field(values, doc):
    """Run the gate and index the resulting fields by name."""
    return {f.field: f for f in run_gate(values, doc, _pack())}


# --- grounding unit tests ------------------------------------------------------


def test_exact_match():
    kind, _ = match_value("1,000,000원", DOC)
    assert kind is MatchKind.EXACT


def test_partial_numeric_match_ignores_formatting():
    # value has no separators/unit but the number exists in the doc
    kind, _ = match_value("1000000", DOC)
    assert kind is MatchKind.PARTIAL_NUMERIC


def test_ungrounded_number_is_none():
    kind, _ = match_value("9,999,999원", DOC)
    assert kind is MatchKind.NONE


def test_eval_expr_safe():
    assert eval_expr("supply * 0.10", {"supply": 1_000_000}) == 100_000


# --- gate: happy path ----------------------------------------------------------


def test_happy_path_all_verified():
    extractor = MockExtractor(
        [
            ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
            ExtractedValue("vat", "100,000원", 100_000, "세액        100,000원"),
            ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
        ]
    )
    values = extractor.extract(DOC, doc_type="tax_invoice")
    fields = run_gate(values, DOC, _pack())

    assert all(f.verdict is Verdict.VERIFIED for f in fields)
    assert all(f.confidence == 1.0 for f in fields)
    assert summarize(fields) == {
        "total": 3,
        "verified": 3,
        "discarded": 0,
        "ungrounded": 0,
    }


# --- gate: numeric hallucination (ungrounded) ----------------------------------


def test_hallucinated_value_is_discarded_at_confidence_zero():
    # 'vat' is a number that does NOT appear anywhere in the document, and its
    # cited quote is fabricated. It must auto-discard at confidence 0.
    extractor = MockExtractor(
        [
            ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
            ExtractedValue("vat", "250,000원", 250_000, "세액  250,000원"),  # hallucinated
            ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
        ]
    )
    fields = _by_field(extractor.extract(DOC, doc_type="tax_invoice"), DOC)

    vat = fields["vat"]
    assert vat.verdict is Verdict.DISCARDED
    assert vat.confidence == 0.0
    assert not vat.grounded  # grounding check failed
    # the failed-check detail is what the viewer tooltip shows
    assert any(c.name == "grounding" and not c.passed for c in vat.checks)


# --- gate: arithmetic rule violation -------------------------------------------


def test_rule_violation_discards_referenced_fields():
    # All three numbers exist verbatim in a doc, so grounding PASSES, but
    # vat (200,000) != supply*10% (100,000) -> arithmetic rule fails.
    doc = (
        "공급가액  1,000,000원\n"
        "세액        200,000원\n"   # wrong VAT, but present in text (grounded)
        "합계금액  1,200,000원\n"
    )
    extractor = MockExtractor(
        [
            ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
            ExtractedValue("vat", "200,000원", 200_000, "세액        200,000원"),
            ExtractedValue("total", "1,200,000원", 1_200_000, "합계금액  1,200,000원"),
        ]
    )
    fields = _by_field(extractor.extract(doc, doc_type="tax_invoice"), doc)

    # vat is grounded but violates vat = supply*10% -> discarded at 0
    assert fields["vat"].grounded is True
    assert fields["vat"].verdict is Verdict.DISCARDED
    assert fields["vat"].confidence == 0.0
    assert any(
        c.name == "vat_equals_supply_x_10pct" and not c.passed for c in fields["vat"].checks
    )

    # The violated invariant taints EVERY field it references: we cannot tell
    # whether `supply` or `vat` is the wrong number, so both are discarded.
    assert fields["supply"].grounded is True
    assert fields["supply"].verdict is Verdict.DISCARDED
    assert fields["supply"].confidence == 0.0

    # total is grounded and only appears in `total = supply + vat`
    # (1,200,000 == 1,000,000 + 200,000), which holds -> total stays verified.
    assert fields["total"].verdict is Verdict.VERIFIED


def test_sum_rule_when_items_present():
    doc = (
        "품목1 공급가  400,000원\n"
        "품목2 공급가  600,000원\n"
        "공급가액  1,000,000원\n"
        "세액        100,000원\n"
        "합계금액  1,100,000원\n"
    )
    values = [
        ExtractedValue("item1_supply", "400,000원", 400_000, "품목1 공급가  400,000원"),
        ExtractedValue("item2_supply", "600,000원", 600_000, "품목2 공급가  600,000원"),
        ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
        ExtractedValue("vat", "100,000원", 100_000, "세액        100,000원"),
        ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
    ]
    fields = _by_field(values, doc)
    assert all(f.verdict is Verdict.VERIFIED for f in fields.values())
    # the sum rule was actually applied to 'supply'
    assert any(c.name == "supply_equals_sum_of_items" for c in fields["supply"].checks)
