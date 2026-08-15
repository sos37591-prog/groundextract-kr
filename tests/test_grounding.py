"""Grounding regression tests: the numeric fuzzy hole and KR negative notation.

Two properties are pinned here, both of which the gate's headline claim ("every
number carries verbatim evidence") depends on:

1. A value that parses as a number is never accepted on an edit-distance
   ratio. Fuzzy matching is for text (상호·품목), where OCR noise is real; for a
   number a one-character difference is a *different amount*, not noise.
2. Korean financial statements write negatives as △/▲ or accounting
   parentheses. Those must parse as negative, without misreading ordinary
   parentheticals such as "(주)" or "(단위: 원)".
"""

from __future__ import annotations

import random

from groundextract import MatchKind, ground_value, match_value
from groundextract.grounding import FUZZY_THRESHOLD, normalize_number_str

DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)


# --- 1) numeric values never reach the FUZZY tier ------------------------------


def test_single_digit_corruption_of_amount_is_ungrounded():
    # Regression: this scored fuzzy ratio 0.90 (>= 0.87) and was reported
    # GROUNDED, i.e. a 900,000원 overstatement passed the evidence gate.
    kind, detail = match_value("1,900,000원", "공급가액  1,000,000원")
    assert kind is MatchKind.NONE
    assert "fuzzy" in detail  # detail must say why a number got no fuzzy pass


def test_no_digit_length_lets_a_corrupted_amount_through():
    """Sweep single-digit corruptions across 4..10 digit amounts.

    Before the fix, 7- and 8-digit amounts (백만~수천만원 — the dominant
    magnitude in tax invoices and financial statements) passed 300/300.
    """
    rng = random.Random(20260815)  # seeded: deterministic, no flakes
    for digits in range(4, 11):
        for _ in range(60):
            n = rng.randrange(10 ** (digits - 1), 10**digits)
            truth = f"{n:,}"
            i = rng.choice([p for p, ch in enumerate(truth) if ch.isdigit()])
            wrong = rng.choice([d for d in "0123456789" if d != truth[i]])
            corrupted = truth[:i] + wrong + truth[i + 1 :]

            kind, _ = match_value(f"{corrupted}원", f"공급가액  {truth}원")
            assert kind is MatchKind.NONE, (
                f"{digits}-digit corruption {corrupted} grounded against {truth}"
            )


def test_correct_amounts_still_ground_despite_formatting():
    # EXACT: verbatim substring
    assert match_value("1,000,000원", DOC)[0] is MatchKind.EXACT
    # PARTIAL_NUMERIC: same number, separators/units differ (both directions)
    assert match_value("1000000", DOC)[0] is MatchKind.PARTIAL_NUMERIC
    assert match_value("1,000,000원", "공급가액 1000000")[0] is MatchKind.PARTIAL_NUMERIC
    assert match_value("₩1,100,000.00", DOC)[0] is MatchKind.PARTIAL_NUMERIC


def test_fabricated_quote_with_corrupted_amount_is_rejected():
    # The model cites a span that does not exist: the document says 3,000,000
    # but the quote claims 7,000,000. The quote used to clear the fuzzy tier
    # (ratio 0.88), after which the value matched EXACTly *inside the forgery*.
    text = "합계잔액시산표\n차변 합계  3,000,000원\n대변 합계  3,000,000원\n"
    check = ground_value("7,000,000원", "차변 합계  7,000,000원", text)
    assert check.passed is False
    assert check.kind is MatchKind.NONE


def test_fuzzy_still_tolerates_ocr_noise_on_text_fields():
    # Non-numeric values keep the OCR-noise allowance the tier exists for.
    kind, detail = match_value("주식회사 한국무역", "공급자  주식회사 한국무억\n")
    assert kind is MatchKind.FUZZY
    assert f"{FUZZY_THRESHOLD}" in detail


def test_text_field_that_is_actually_different_is_still_rejected():
    kind, _ = match_value("주식회사 한국무역", "공급자  대한상사 물류센터\n")
    assert kind is MatchKind.NONE


# --- 2) △ / ▲ / accounting parentheses parse as negative -----------------------


def test_triangle_and_paren_notation_parse_as_negative():
    # 결손금·감소액 are written this way in Korean financial statements.
    assert normalize_number_str("△1,234") == "-1234"
    assert normalize_number_str("▲1,234") == "-1234"
    assert normalize_number_str("(1,234)") == "-1234"
    assert normalize_number_str("△1,234원") == "-1234"
    assert normalize_number_str("( 1,234 )") == "-1234"
    # unchanged behaviour for the plain forms
    assert normalize_number_str("-1,234") == "-1234"
    assert normalize_number_str("1,234") == "1234"
    assert normalize_number_str("1,000,000원") == "1000000"
    assert normalize_number_str("₩ 1,000,000.00") == "1000000"


def test_ordinary_korean_parentheticals_are_not_read_as_negative():
    # The parenthesis rule only fires when the parens wrap a bare number.
    assert normalize_number_str("(주)일송무역") is None
    assert normalize_number_str("(단위: 원)") is None
    assert normalize_number_str("(단위: 천원)") is None
    # a number *inside* a longer parenthetical stays positive
    assert normalize_number_str("(단위: 1,000원)") == "1000"
    assert normalize_number_str("(주)일송무역 1,234원") == "1234"
    # △ used as a bullet, detached from the figure, is not a sign
    assert normalize_number_str("△ 매출액 1,234") == "1234"
    # no digits at all
    assert normalize_number_str("공급가액") is None


def test_zero_never_normalizes_to_negative_zero():
    assert normalize_number_str("(0)") == normalize_number_str("0") == "0"


def test_grounding_respects_the_sign():
    # -1,234 grounds against the accounting form of the same number...
    kind, _ = match_value("-1,234", "당기순손실  (1,234)원")
    assert kind is MatchKind.PARTIAL_NUMERIC
    kind, _ = match_value("-1,234", "이월결손금  △1,234원")
    assert kind is MatchKind.PARTIAL_NUMERIC
    # ...but a sign-dropped value does not match a negative figure.
    kind, _ = match_value("1234", "이월결손금  △1,234원")
    assert kind is MatchKind.NONE
