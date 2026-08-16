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
    assert summarize(fields, _pack()) == {
        "total": 3,
        "verified": 3,
        "discarded": 0,
        "ungrounded": 0,
        # the caller can see verification actually ran: two invariants applied
        # (the per-item sum rule needs line items, which aren't extracted here)
        "rule_pack": "tax_invoice",
        "rules_applied": 2,
    }


def test_summarize_reports_when_no_arithmetic_ran():
    values = [ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원")]
    summary = summarize(run_gate(values, DOC, None), None)
    assert summary["rule_pack"] is None
    assert summary["rules_applied"] == 0
    assert summary["verified"] == 0  # nothing verified it, so nothing is verified


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


# --- gate: fail-closed when no arithmetic rule reaches a number ----------------
#
# Regression table for the "0 rules applied => vacuously verified" bypass.
# The scenario is a field swap: 250,000원 really is printed on the document (as
# a unit price), so grounding PASSES and only arithmetic can catch it being
# reported as the VAT. If the arithmetic never runs, a wrong number sails
# through — so "no rule ran" must fail, never pass.

SWAP_DOC = (
    "전자세금계산서\n"
    "품목 단가  250,000원\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)


def _swap_values(with_number: bool = True):
    """supply/total correct, vat swapped to a real-but-wrong figure."""
    rows = [
        ("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
        ("vat", "250,000원", 250_000, "품목 단가  250,000원"),  # field swap
        ("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
    ]
    return [
        ExtractedValue(f, raw, num if with_number else None, quote) for f, raw, num, quote in rows
    ]


def _pack_named(doc_type: str):
    return load_rule_pack(RULES.parent / f"{doc_type}.yaml")


def test_field_swap_is_caught_with_the_matching_rule_pack():
    fields = {f.field: f for f in run_gate(_swap_values(), SWAP_DOC, _pack())}
    assert all(f.grounded for f in fields.values())  # every figure is on the document
    # Both invariants break. `vat` and `supply` each appear in both, so either
    # could be the liar and both are discarded; `total` appears in only one, so
    # it cannot be the single explanation and is spared.
    assert fields["vat"].verdict is Verdict.DISCARDED
    assert fields["supply"].verdict is Verdict.DISCARDED
    assert fields["total"].verdict is Verdict.VERIFIED
    assert any(
        c.name == "vat_equals_supply_x_10pct" and not c.passed for c in fields["vat"].checks
    )


def test_missing_rule_pack_discards_numbers_instead_of_verifying_them():
    # No pack => nothing arithmetic ran. `all([])` is True, which used to make
    # every grounded number "verified" without any verification.
    fields = run_gate(_swap_values(), SWAP_DOC, None)
    assert all(f.verdict is Verdict.DISCARDED for f in fields)
    for f in fields:
        assert any(c.name == "rules_applied" and not c.passed for c in f.checks)
        assert f.confidence == 0.0


def test_rule_pack_for_another_doc_type_does_not_verify_these_fields():
    # Valid packs, wrong document type: their rules reference debit_total /
    # total_assets etc., so not one of them applies to supply/vat/total.
    for doc_type in ("statement", "balance_sheet"):
        fields = run_gate(_swap_values(), SWAP_DOC, _pack_named(doc_type))
        assert all(f.verdict is Verdict.DISCARDED for f in fields), doc_type
        assert all(
            any(c.name == "rules_applied" and not c.passed for c in f.checks) for f in fields
        ), doc_type


def test_number_omitted_is_still_arithmetically_verified():
    # `number` is optional in the MCP value schema, so an ordinary client may
    # leave it out. That must not skip the arithmetic: the number is parsed
    # from `raw` (the same string grounding checks) and the rules still fire.
    fields = {f.field: f for f in run_gate(_swap_values(with_number=False), SWAP_DOC, _pack())}
    assert all(f.value.number is None for f in fields.values())
    # same verdicts as when `number` is supplied — the rules fired either way
    assert fields["vat"].verdict is Verdict.DISCARDED
    assert fields["supply"].verdict is Verdict.DISCARDED
    assert fields["total"].verdict is Verdict.VERIFIED
    assert any(
        c.name == "vat_equals_supply_x_10pct" and not c.passed for c in fields["vat"].checks
    )


def test_number_omitted_without_rules_is_discarded_too():
    fields = run_gate(_swap_values(with_number=False), SWAP_DOC, None)
    assert all(f.verdict is Verdict.DISCARDED for f in fields)
    assert all(any(c.name == "rules_applied" and not c.passed for c in f.checks) for f in fields)


def test_correct_extraction_still_verifies_without_the_number_field():
    # Fail-closed must not mean "always closed": the same client that omits
    # `number` gets its correct figures verified.
    values = [
        ExtractedValue("supply", "1,000,000원", None, "공급가액  1,000,000원"),
        ExtractedValue("vat", "100,000원", None, "세액        100,000원"),
        ExtractedValue("total", "1,100,000원", None, "합계금액  1,100,000원"),
    ]
    fields = run_gate(values, DOC, _pack())
    assert all(f.verdict is Verdict.VERIFIED for f in fields)


def test_textual_field_needs_no_arithmetic_rule():
    # A value carrying no number (상호·품목 등) has no invariant to satisfy —
    # grounding alone decides it, otherwise the gate would discard every
    # non-numeric field ever extracted.
    doc = "상호 주식회사 가나다\n공급가액  1,000,000원\n"
    values = [
        ExtractedValue("supplier_name", "주식회사 가나다", None, "상호 주식회사 가나다"),
        ExtractedValue("buyer_name", "주식회사 라마바", None, "상호 주식회사 라마바"),
    ]
    fields = {f.field: f for f in run_gate(values, doc, _pack())}
    assert fields["supplier_name"].verdict is Verdict.VERIFIED
    assert not any(c.name == "rules_applied" for c in fields["supplier_name"].checks)
    # ...and an invented company name is still discarded on grounding alone
    assert fields["buyer_name"].verdict is Verdict.DISCARDED


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
