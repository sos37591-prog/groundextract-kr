"""표준손익계산서 · 법인세 과세표준 및 세액조정계산서 rule tests.

Both packs were written against a real filed return (법인세법 시행규칙 별지
제3호서식/제3호의3서식) — the figures here are synthetic but satisfy exactly the
identities the forms print next to their own line items.

What these two forms add over 재무상태표 is *depth*: 손익계산서 is a subtraction
chain, so a misread in the middle breaks the link above it and the one below,
and the gate has two failing rules to intersect instead of one. That is the
difference between "some field in this rule is wrong" and "this field is wrong".
"""

from __future__ import annotations

from pathlib import Path

from groundextract import ExtractedValue, Verdict, load_pack, run_gate

ROOT = Path(__file__).resolve().parent.parent

# --- 표준손익계산서 -------------------------------------------------------------

IS_PACK = load_pack("income_statement")

# 매출 500,000,000 − 매출원가 180,000,000 = 매출총손익 320,000,000
# − 판관비 210,000,000 = 영업손익 110,000,000
# + 영업외수익 3,000,000 − 영업외비용 1,000,000 = 차감전 112,000,000
# − 법인세비용 12,000,000 = 당기순손익 100,000,000
IS_CLEAN = {
    "revenue": 500_000_000,
    "cogs": 180_000_000,
    "gross_profit": 320_000_000,
    "sga": 210_000_000,
    "operating_income": 110_000_000,
    "non_operating_income": 3_000_000,
    "non_operating_expense": 1_000_000,
    "pretax_income": 112_000_000,
    "income_tax_expense": 12_000_000,
    "net_income": 100_000_000,
}

# The filed layout: 계정과목 / 코드 / 금액, no 원 in the amount column.
IS_DOC = "\n".join(
    [
        "표준손익계산서",
        "(일반법인용)                                        (단위: 원)",
        f"Ⅰ.매출액 001 {IS_CLEAN['revenue']:,}",
        f"Ⅱ.매출원가 035 {IS_CLEAN['cogs']:,}",
        f"Ⅲ.매출총손익 066 {IS_CLEAN['gross_profit']:,}",
        f"Ⅳ.판매비와관리비 067 {IS_CLEAN['sga']:,}",
        f"Ⅴ.영업손익 129 {IS_CLEAN['operating_income']:,}",
        f"Ⅵ.영업외수익 130 {IS_CLEAN['non_operating_income']:,}",
        f"Ⅶ.영업외비용 179 {IS_CLEAN['non_operating_expense']:,}",
        f"Ⅷ.법인세비용차감전손익 217 {IS_CLEAN['pretax_income']:,}",
        f"Ⅸ.법인세비용 218 {IS_CLEAN['income_tax_expense']:,}",
        f"Ⅹ.당기순손익 219 {IS_CLEAN['net_income']:,}",
    ]
) + "\n"


def _gate(clean: dict[str, int], pack, text: str, overrides: dict[str, int] | None = None):
    overrides = overrides or {}
    vals = [
        ExtractedValue(k, f"{overrides.get(k, v):,}원", float(overrides.get(k, v)))
        for k, v in clean.items()
    ]
    return {f.field: f for f in run_gate(vals, text, pack)}


def test_income_statement_clean_all_verified():
    fields = _gate(IS_CLEAN, IS_PACK, IS_DOC)
    assert all(f.verdict is Verdict.VERIFIED for f in fields.values()), {
        k: v.failed_checks for k, v in fields.items() if v.verdict is not Verdict.VERIFIED
    }


def test_income_statement_amount_column_grounds_without_the_won_suffix():
    """The form prints "320,000,000"; an extractor emits "320,000,000원".

    That is the PARTIAL_NUMERIC tier doing its job — the same figure written with
    a currency unit the document does not repeat. If this regressed to a plain
    substring test, every filed return would come back ungrounded.
    """
    fields = _gate(IS_CLEAN, IS_PACK, IS_DOC)
    assert fields["gross_profit"].grounded is True


def test_income_statement_misread_in_the_chain_is_localized():
    """판관비 misread by one digit, and the misread figure is in the text.

    Grounding passes. 영업손익 = 매출총손익 − 판관비 breaks, and that is the only
    rule 판관비 appears in — but 매출총손익 is corroborated by the rule above it and
    영업손익 by the rule below, so both are set aside and 판관비 is named alone.
    """
    bad_sga = 210_000_000 + 1_000_000
    text = IS_DOC.replace(
        f"Ⅳ.판매비와관리비 067 {IS_CLEAN['sga']:,}",
        f"Ⅳ.판매비와관리비 067 {bad_sga:,}",
    )
    fields = _gate(IS_CLEAN, IS_PACK, text, {"sga": bad_sga})

    assert fields["sga"].grounded is True          # the wrong figure is really there
    assert fields["sga"].verdict is Verdict.DISCARDED
    assert fields["sga"].confidence == 0.0
    # the neighbours a passing rule vouches for survive
    for survivor in ("revenue", "cogs", "gross_profit", "operating_income", "net_income"):
        assert fields[survivor].verdict is Verdict.VERIFIED, survivor


def test_income_statement_hallucinated_net_income_is_discarded():
    """A 당기순손익 that appears nowhere: grounding and arithmetic both refuse."""
    fields = _gate(IS_CLEAN, IS_PACK, IS_DOC, {"net_income": 99_999_999})
    assert fields["net_income"].grounded is False
    assert fields["net_income"].verdict is Verdict.DISCARDED


# --- 법인세 과세표준 및 세액조정계산서 ---------------------------------------------

CTR_PACK = load_pack("corporate_tax_return")

# 101 + 102 − 103 = 104 ; 104 + 105 − 106 = 107 ;
# 107 − 109 − 110 − 111 = 112 ; 122 − 123 + 124 = 125
CTR_CLEAN = {
    "net_income_per_books": 100_000_000,
    "gross_additions": 8_000_000,
    "gross_deductions": 3_000_000,
    "adjusted_income": 105_000_000,
    "donation_excess": 2_000_000,
    "donation_carryover_deduction": 1_000_000,
    "income_for_year": 106_000_000,
    "loss_carryforward": 4_000_000,
    "nontaxable_income": 1_000_000,
    "income_deduction": 1_000_000,
    "taxable_income": 100_000_000,
    "tax_after_credits": 18_000_000,
    "credits_excluded_from_min_tax": 5_000_000,
    "additional_tax": 500_000,
    "tax_payable": 13_500_000,
}

CTR_DOC = "\n".join(
    [
        "법인세 과세표준 및 세액조정계산서",
        f"101 결 산 서 상 당 기 순 손 익 01 {CTR_CLEAN['net_income_per_books']:,}",
        f"102 익 금 산 입 02 {CTR_CLEAN['gross_additions']:,}",
        f"103 손 금 산 입 03 {CTR_CLEAN['gross_deductions']:,}",
        f"104 차 가 감 소 득 금 액 04 {CTR_CLEAN['adjusted_income']:,} (101＋102－103)",
        f"105 기 부 금 한 도 초 과 액 05 {CTR_CLEAN['donation_excess']:,}",
        f"106 기부금한도초과이월액 손금산입 54 {CTR_CLEAN['donation_carryover_deduction']:,}",
        f"107 각 사 업 연 도 소 득 금 액 06 {CTR_CLEAN['income_for_year']:,} (104+105-106)",
        f"109 이 월 결 손 금 07 {CTR_CLEAN['loss_carryforward']:,}",
        f"110 비 과 세 소 득 08 {CTR_CLEAN['nontaxable_income']:,}",
        f"111 소 득 공 제 09 {CTR_CLEAN['income_deduction']:,}",
        f"112 과 세 표 준 10 {CTR_CLEAN['taxable_income']:,} (108－109－110-111)",
        f"122 차 감 세 액 18 {CTR_CLEAN['tax_after_credits']:,}",
        f"123 최저한세 적용제외 공제감면세액 19 {CTR_CLEAN['credits_excluded_from_min_tax']:,}",
        f"124 가 산 세 액 20 {CTR_CLEAN['additional_tax']:,}",
        f"125 가 감 계 21 {CTR_CLEAN['tax_payable']:,} (122-123+124)",
    ]
) + "\n"


def test_corporate_tax_return_clean_all_verified():
    fields = _gate(CTR_CLEAN, CTR_PACK, CTR_DOC)
    assert all(f.verdict is Verdict.VERIFIED for f in fields.values()), {
        k: v.failed_checks for k, v in fields.items() if v.verdict is not Verdict.VERIFIED
    }


def test_corporate_tax_return_misread_breaks_two_links():
    """104 차가감소득금액 misread: it is the target of one rule and a term of the next.

    Being in two rules is what makes it nameable — the intersection of "something
    in 101/102/103/104 is wrong" and "something in 104/105/106/107 is wrong" is
    104 itself, provided the neighbours are vouched for elsewhere.
    """
    bad = CTR_CLEAN["adjusted_income"] + 100_000
    text = CTR_DOC.replace(
        f"04 {CTR_CLEAN['adjusted_income']:,} (101", f"04 {bad:,} (101"
    )
    fields = _gate(CTR_CLEAN, CTR_PACK, text, {"adjusted_income": bad})

    assert fields["adjusted_income"].grounded is True
    assert fields["adjusted_income"].verdict is Verdict.DISCARDED
    # the 세액 half of the form is governed by a rule of its own and is untouched
    for survivor in ("tax_after_credits", "credits_excluded_from_min_tax", "tax_payable"):
        assert fields[survivor].verdict is Verdict.VERIFIED, survivor


def test_corporate_tax_return_omits_the_progressive_rate_rule():
    """산출세액 = 과세표준 × 세율 must stay out: 법인세 is a bracketed schedule.

    Encoding it as a multiplication would discard correct 산출세액 for every filer
    above the first bracket. The absence is deliberate, so pin it — a future
    contributor adding it would break real returns silently.
    """
    names = {r.name for r in CTR_PACK.rules}
    assert not any("rate" in n or "calculated_tax" in n for n in names)
    referenced = {
        side.get("field")
        for r in CTR_PACK.rules
        for side in (r.lhs or {}, r.rhs or {})
    }
    assert "tax_rate" not in referenced
    assert "calculated_tax" not in referenced
