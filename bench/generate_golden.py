"""NumHall-KR synthetic golden-set generator (deterministic, key-free).

Writes ``gen_*.json`` golden documents into ``bench/golden/`` alongside the
hand-written goldens (which are never overwritten): Korean tax invoices
(전자세금계산서), trial-balance statements (합계잔액시산표) and balance sheets
(표준재무상태표) with realistic layouts - 공급가액/세액/합계금액, 차변/대변 합계,
유동·비유동/부채·자본 총계, varied thousands-comma amounts.

Roughly 20% of the labeled fields carry an injected extraction error
(at most one per document, so labels stay unambiguous):

  * ``ungrounded``  - the number is replaced by one that appears nowhere in
    the document and the cited quote is fabricated to match. Verbatim
    grounding alone catches these (``expected_verdict: discarded``).
  * ``field-swap``  - the extractor grabs another field's number that DOES
    exist verbatim in the document, citing the real span it came from:
    grounding passes, but an arithmetic invariant breaks. Only the rule
    engine catches these (``expected_verdict: discarded``) - the case
    grounding alone cannot see.

Everything is driven by a single seeded ``random.Random``, so a given
(seed, count-tax, count-stmt) always regenerates identical files. Every
candidate document is validated through the real gate before being written:
each labeled-bad field must come out DISCARDED (bench recall stays 1.0),
clean docs must come out fully VERIFIED, swap fields must stay grounded, and
failing candidates are deterministically re-rolled.

Run:  python bench/generate_golden.py
        [--count-tax 30 --count-stmt 10 --count-balance 10 --seed 42]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from groundextract.gate import run_gate  # noqa: E402
from groundextract.grounding import normalize_number_str  # noqa: E402
from groundextract.models import ExtractedValue, Verdict  # noqa: E402
from groundextract.rules import RulePack, load_rule_pack  # noqa: E402

RULES_DIR = ROOT / "rules"
DEFAULT_OUT_DIR = ROOT / "bench" / "golden"

BAD_FIELD_RATE = 0.20  # target: ~20% of all labeled fields carry an injected error
SWAP_SHARE = 0.4       # of the injected errors, this share are field-swaps
MIN_SWAPS = 3          # always include a few arithmetic-only catches

#: Share of documents that may carry an injection. At most one error goes into a
#: document (so a labeled fault is never ambiguous), which puts a ceiling on the
#: field-level rate: a 15-field 세액조정계산서 contributes fifteen fields but only
#: one possible error, so BAD_FIELD_RATE became unreachable once the wide forms
#: joined the suite. Without this cap the shortfall was spent by injecting into
#: *every* document, leaving no clean one - and a clean document is the only case
#: that exercises "the gate must not discard anything", which is the generator's
#: own correctness check in :func:`_validate`. The realised rate is reported.
MAX_BAD_DOC_SHARE = 0.75
MAX_DOC_ATTEMPTS = 50  # deterministic re-rolls if a candidate fails the gate

# Same number-token pattern the grounding matcher uses (PARTIAL_NUMERIC tier).
_NUM_TOKEN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Fields the rule packs reference - injection targets are chosen among these.
_RULE_FIELDS = {
    "tax_invoice": ("supply", "vat", "total"),
    "statement": ("debit_total", "credit_total"),
    "balance_sheet": (
        "current_assets",
        "noncurrent_assets",
        "total_assets",
        "total_liabilities",
        "total_equity",
        "total_liab_equity",
    ),
    "income_statement": (
        "revenue",
        "cogs",
        "gross_profit",
        "sga",
        "operating_income",
        "non_operating_income",
        "non_operating_expense",
        "pretax_income",
        "income_tax_expense",
        "net_income",
    ),
    "corporate_tax_return": (
        "net_income_per_books",
        "gross_additions",
        "gross_deductions",
        "adjusted_income",
        "donation_excess",
        "donation_carryover_deduction",
        "income_for_year",
        "loss_carryforward",
        "nontaxable_income",
        "income_deduction",
        "taxable_income",
        "tax_after_credits",
        "credits_excluded_from_min_tax",
        "additional_tax",
        "tax_payable",
    ),
}

#: Smallest amount any generated field may carry. Two injection mechanics depend
#: on it. ``_inject_ungrounded`` rewrites a quote with ``str.replace`` on the
#: comma-formatted amount, so an amount that is a substring of the line's *code
#: number* would corrupt the code as well - "035 0" with the amount 0 becomes
#: "35 <fake>". At >= 100,000 every amount carries a comma and no three-digit
#: form code can collide with it. It also keeps a zero out of the labels, where
#: grounding would be vacuous because a statement is full of zeros.
MIN_AMOUNT = 100_000

_COMPANIES = [
    "주식회사 한빛상사", "(주)미래유통", "동성물산 주식회사", "(주)가온소프트",
    "청람테크 주식회사", "(주)세정기업", "한결시스템 주식회사", "(주)누리상사",
    "대현전자 주식회사", "(주)일송무역",
]
_ITEMS = [
    "소프트웨어 개발", "시스템 유지보수", "클라우드 이용료", "전산장비 납품",
    "경영 컨설팅 용역", "데이터베이스 구축", "네트워크 공사", "보안 솔루션 공급",
    "홈페이지 제작", "키오스크 설치",
]
_DEBIT_ACCOUNTS = ["현금", "보통예금", "외상매출금", "미수금", "비품", "소모품비", "지급수수료"]
_CREDIT_ACCOUNTS = ["외상매입금", "미지급금", "자본금", "매출", "단기차입금", "선수금"]

# 표준재무상태표·표준손익계산서 line items as (계정과목, 코드). The code numbers are
# the real ones from 법인세법 시행규칙 [별지 제3호의3서식]: the generated documents
# reproduce the form's three-column layout (계정과목 / 코드 / 금액) rather than a
# prose approximation, so the benchmark exercises the same text an OCR pass over a
# filed return actually produces - including the stray number tokens the code
# column adds next to every amount.
_CURRENT_ASSETS = [
    ("현금 및 현금성자산", "003"), ("단기금융상품", "005"), ("매출채권", "009"),
    ("재고자산", "044"), ("선급금", "069"),
]
_NONCURRENT_ASSETS = [
    ("투자자산", "081"), ("유형자산", "110"), ("무형자산", "169"),
    ("기타비유동자산", "194"), ("보증금", "217"),
]
_LIABILITIES = [
    ("매입채무", "230"), ("단기차입금", "234"), ("미지급금", "238"),
    ("예수금", "246"), ("장기차입금", "285"),
]
_EQUITY = [("자본금", "334"), ("자본잉여금", "337"), ("이익잉여금", "372")]
_REVENUE_ITEMS = [("제품매출", "002"), ("상품매출", "010"), ("서비스매출", "025")]
_SGA_ITEMS = [
    ("급여", "068"), ("복리후생비", "079"), ("여비교통비", "080"),
    ("지급수수료", "104"), ("소모품비", "108"),
]


@dataclass
class GenField:
    """One labeled extraction: starts as the ground truth, may get an error."""

    field: str
    raw: str
    number: int
    grounding_quote: str
    expected: str = "verified"

    def to_json_obj(self) -> dict[str, object]:
        return {
            "field": self.field,
            "raw": self.raw,
            "number": self.number,
            "grounding_quote": self.grounding_quote,
            "expected_verdict": self.expected,
        }


@dataclass
class BuiltDoc:
    doc_type: str
    full_text: str
    fields: list[GenField]
    # (amount, verbatim line) pairs an extractor could plausibly wrongly grab.
    swap_sources: list[tuple[int, str]]


# --- document builders ---------------------------------------------------------


def _reg_no(rng: random.Random) -> str:
    return f"{rng.randrange(100, 1000)}-{rng.randrange(10, 100)}-{rng.randrange(10000, 100000)}"


def _build_tax(rng: random.Random, with_items: bool) -> BuiltDoc:
    supply = rng.randrange(100, 50_001) * 1_000  # 공급가액 10만 ~ 5,000만 원
    vat = supply // 10
    total = supply + vat
    mm, dd = rng.randrange(1, 13), rng.randrange(1, 29)
    supplier, buyer = rng.sample(_COMPANIES, 2)
    approval = (
        f"2026{mm:02d}{dd:02d}-{rng.randrange(10_000_000, 100_000_000)}"
        f"-{rng.randrange(10_000_000, 100_000_000)}"
    )
    supply_line = f"공급가액  {supply:,}원"
    vat_line = f"세액  {vat:,}원"
    total_line = f"합계금액  {total:,}원"

    lines = [
        "전자세금계산서",
        f"승인번호: {approval}",
        f"작성일자: 2026-{mm:02d}-{dd:02d}",
        f"공급자: {supplier} (등록번호 {_reg_no(rng)})",
        f"공급받는자: {buyer} (등록번호 {_reg_no(rng)})",
        "",
    ]
    fields = [
        GenField("supply", f"{supply:,}원", supply, supply_line),
        GenField("vat", f"{vat:,}원", vat, vat_line),
        GenField("total", f"{total:,}원", total, total_line),
    ]
    swap_sources = [(supply, supply_line), (vat, vat_line), (total, total_line)]

    if with_items:
        item1 = rng.randrange(1, supply // 1_000) * 1_000
        item2 = supply - item1
        name1, name2 = rng.sample(_ITEMS, 2)
        item1_line = f"품목: {name1}  공급가액 {item1:,}원  세액 {item1 // 10:,}원"
        item2_line = f"품목: {name2}  공급가액 {item2:,}원  세액 {item2 // 10:,}원"
        lines += [item1_line, item2_line, ""]
        fields += [
            GenField("item1_supply", f"{item1:,}원", item1, item1_line),
            GenField("item2_supply", f"{item2:,}원", item2, item2_line),
        ]
        swap_sources += [(item1, item1_line), (item2, item2_line)]
    else:
        lines += [f"품목: {rng.choice(_ITEMS)}", ""]

    lines += [supply_line, vat_line, total_line]
    return BuiltDoc("tax_invoice", "\n".join(lines) + "\n", fields, swap_sources)


def _split_amount(rng: random.Random, units: int, parts: int) -> list[int]:
    """Split ``units`` (x1,000원) into ``parts`` positive multiples of 1,000원."""
    cuts = sorted(rng.sample(range(1, units), parts - 1))
    return [(b - a) * 1_000 for a, b in pairwise([0, *cuts, units])]


def _build_stmt(rng: random.Random) -> BuiltDoc:
    units = rng.randrange(500, 80_001)  # 총액 50만 ~ 8,000만 원
    total = units * 1_000
    debit_parts = _split_amount(rng, units, rng.randrange(2, 4))
    credit_parts = _split_amount(rng, units, rng.randrange(2, 4))
    debit_names = rng.sample(_DEBIT_ACCOUNTS, len(debit_parts))
    credit_names = rng.sample(_CREDIT_ACCOUNTS, len(credit_parts))
    company = rng.choice(_COMPANIES)
    mm, dd = rng.randrange(1, 13), rng.randrange(1, 29)

    debit_line = f"차변 합계  {total:,}원"
    credit_line = f"대변 합계  {total:,}원"
    account_lines = [
        f"{name}  차변 {amt:,}원"
        for name, amt in zip(debit_names, debit_parts, strict=True)
    ] + [
        f"{name}  대변 {amt:,}원"
        for name, amt in zip(credit_names, credit_parts, strict=True)
    ]
    lines = [
        "합계잔액시산표",
        f"{company}  2026년 {mm:02d}월 {dd:02d}일 현재",
        "(단위: 원)",
        "",
        "계정과목  차변  대변",
        *account_lines,
        "",
        debit_line,
        credit_line,
    ]
    fields = [
        GenField("debit_total", f"{total:,}원", total, debit_line),
        GenField("credit_total", f"{total:,}원", total, credit_line),
    ]
    swap_sources = [
        (amt, line)
        for amt, line in zip(debit_parts + credit_parts, account_lines, strict=True)
    ]
    return BuiltDoc("statement", "\n".join(lines) + "\n", fields, swap_sources)


def _amount(rng: random.Random, lo: int, hi: int) -> int:
    """A realistic KRW figure in [lo, hi]: a multiple of 1,000 and >= MIN_AMOUNT."""
    lo = max(lo, MIN_AMOUNT)
    hi = max(hi, lo + 1_000)
    return rng.randrange(lo // 1_000, hi // 1_000 + 1) * 1_000


def _form_header(rng: random.Random, title: str, subtitle: str, company: str) -> list[str]:
    """The masthead every 법인세법 시행규칙 별지서식 carries above its table."""
    return [
        title,
        "(일반법인용)                                        (단위: 원)",
        f"법인명 {company}",
        f"사업자등록번호 {_reg_no(rng)}",
        subtitle,
        "계 정 과 목 코드 금 액",
    ]


def _rows(rng: random.Random, catalog: list[tuple[str, str]], amounts: list[int]) -> list[str]:
    """Detail lines in the form's three-column layout: 계정과목 / 코드 / 금액."""
    picked = rng.sample(catalog, len(amounts))
    return [
        f"  {name} {code} {amt:,}"
        for (name, code), amt in zip(picked, amounts, strict=True)
    ]


def _build_balance(rng: random.Random) -> BuiltDoc:
    """표준재무상태표 — three overlapping invariants over six totals.

    The asset side and the liability/equity side each sum to the same figure, and
    that figure is itself a field. Every total is therefore covered by two rules,
    which is what lets the gate say *which* of them broke rather than discarding
    the whole statement.

    The layout is the filed one — 계정과목 / 코드 / 금액 columns, roman-numeral
    section headers, no 원 suffix on the amount column — because that is the text
    an OCR pass over a real return yields. Extracted values still carry 원, which
    is what an extractor emits, so the grounding tier under test is
    PARTIAL_NUMERIC rather than a plain substring hit.
    """
    current_parts = _split_amount(rng, rng.randrange(300, 40_001), rng.randrange(2, 4))
    noncurrent_parts = _split_amount(rng, rng.randrange(200, 30_001), rng.randrange(2, 4))
    current = sum(current_parts)
    noncurrent = sum(noncurrent_parts)
    assets = current + noncurrent

    # Split the same total across the funding side (부채 + 자본 = 자산).
    liabilities = rng.randrange(1, assets // 1_000) * 1_000
    equity = assets - liabilities
    cur_liab_parts = _split_amount(rng, liabilities // 1_000, rng.randrange(2, 4))
    equity_parts = _split_amount(rng, equity // 1_000, rng.randrange(2, 4))

    company = rng.choice(_COMPANIES)
    year = rng.randrange(2022, 2027)

    current_lines = _rows(rng, _CURRENT_ASSETS, current_parts)
    noncurrent_lines = _rows(rng, _NONCURRENT_ASSETS, noncurrent_parts)
    liab_lines = _rows(rng, _LIABILITIES, cur_liab_parts)
    equity_lines = _rows(rng, _EQUITY, equity_parts)

    current_line = f"Ⅰ.유동자산 001 {current:,}"
    noncurrent_line = f"Ⅱ.비유동자산 080 {noncurrent:,}"
    assets_line = f"자산총계(Ⅰ+Ⅱ) 228 {assets:,}"
    liab_line = f"부채총계(Ⅰ+Ⅱ) 333 {liabilities:,}"
    equity_line = f"자본총계(Ⅲ+～Ⅶ) 382 {equity:,}"
    liab_equity_line = f"부채와 자본총계 383 {assets:,}"

    lines = [
        *_form_header(rng, "표준재무상태표", f"{year}년12월31일 현재", company),
        current_line,
        *current_lines,
        noncurrent_line,
        *noncurrent_lines,
        assets_line,
        "Ⅰ.유동부채 229",
        *liab_lines,
        liab_line,
        *equity_lines,
        equity_line,
        liab_equity_line,
    ]
    fields = [
        GenField("current_assets", f"{current:,}원", current, current_line),
        GenField("noncurrent_assets", f"{noncurrent:,}원", noncurrent, noncurrent_line),
        GenField("total_assets", f"{assets:,}원", assets, assets_line),
        GenField("total_liabilities", f"{liabilities:,}원", liabilities, liab_line),
        GenField("total_equity", f"{equity:,}원", equity, equity_line),
        GenField("total_liab_equity", f"{assets:,}원", assets, liab_equity_line),
    ]
    swap_sources = [
        (amt, line)
        for amt, line in zip(
            current_parts + noncurrent_parts + cur_liab_parts + equity_parts,
            current_lines + noncurrent_lines + liab_lines + equity_lines,
            strict=True,
        )
    ]
    return BuiltDoc("balance_sheet", "\n".join(lines) + "\n", fields, swap_sources)


def _build_income(rng: random.Random) -> BuiltDoc:
    """표준손익계산서 — one subtraction chain, four links.

    Every subtotal is the previous one adjusted by a line group, so a misread in
    the middle breaks the link above it *and* the one below. The last link,
    당기순손익, is the figure both 재무상태표(이익잉여금) and the 법인세 조정계산서
    re-state, which is why this form is the hinge of a filed return.
    """
    revenue = _amount(rng, 20_000_000, 900_000_000)
    cogs = _amount(rng, 1_000_000, max(2_000_000, revenue // 2))
    gross_profit = revenue - cogs
    sga = _amount(rng, 1_000_000, max(2_000_000, gross_profit - 1_000_000))
    operating_income = gross_profit - sga
    non_operating_income = _amount(rng, MIN_AMOUNT, 5_000_000)
    non_operating_expense = _amount(rng, MIN_AMOUNT, 4_000_000)
    pretax_income = operating_income + non_operating_income - non_operating_expense
    income_tax_expense = _amount(rng, MIN_AMOUNT, max(1_000_000, abs(pretax_income) // 5))
    net_income = pretax_income - income_tax_expense

    company = rng.choice(_COMPANIES)
    year = rng.randrange(2022, 2027)
    revenue_lines = _rows(rng, _REVENUE_ITEMS, [revenue])
    sga_lines = _rows(rng, _SGA_ITEMS, _split_amount(rng, sga // 1_000, rng.randrange(2, 4)))

    rows = [
        ("revenue", f"Ⅰ.매출액 001 {revenue:,}", revenue),
        ("cogs", f"Ⅱ.매출원가 035 {cogs:,}", cogs),
        ("gross_profit", f"Ⅲ.매출총손익 066 {gross_profit:,}", gross_profit),
        ("sga", f"Ⅳ.판매비와관리비 067 {sga:,}", sga),
        ("operating_income", f"Ⅴ.영업손익 129 {operating_income:,}", operating_income),
        ("non_operating_income", f"Ⅵ.영업외수익 130 {non_operating_income:,}",
         non_operating_income),
        ("non_operating_expense", f"Ⅶ.영업외비용 179 {non_operating_expense:,}",
         non_operating_expense),
        ("pretax_income", f"Ⅷ.법인세비용차감전손익 217 {pretax_income:,}", pretax_income),
        ("income_tax_expense", f"Ⅸ.법인세비용 218 {income_tax_expense:,}", income_tax_expense),
        ("net_income", f"Ⅹ.당기순손익 219 {net_income:,}", net_income),
    ]
    lines = [
        *_form_header(
            rng, "표준손익계산서",
            f"{year}년 01월 01일 부터 {year}년 12월 31일 까지", company,
        ),
        rows[0][1], *revenue_lines,
        rows[1][1], rows[2][1],
        rows[3][1], *sga_lines,
        *[r[1] for r in rows[4:]],
    ]
    fields = [GenField(name, f"{amt:,}원", amt, line) for name, line, amt in rows]
    swap_sources = [
        (int(_NUM_TOKEN.findall(ln)[-1].replace(",", "")), ln)
        for ln in revenue_lines + sga_lines
    ]
    return BuiltDoc("income_statement", "\n".join(lines) + "\n", fields, swap_sources)


def _build_taxreturn(rng: random.Random) -> BuiltDoc:
    """법인세 과세표준 및 세액조정계산서 — the form that prints its own arithmetic.

    Each line carries its formula in the margin — "(101＋102－103)", "(122-123+124)"
    — so the invariants are transcribed from the document rather than supplied as
    domain knowledge. 산출세액 is deliberately absent from the labels: 법인세 is a
    bracketed schedule, and the pack does not encode a rule it cannot express.
    """
    books = _amount(rng, 5_000_000, 400_000_000)
    additions = _amount(rng, MIN_AMOUNT, 20_000_000)
    deductions = _amount(rng, MIN_AMOUNT, 10_000_000)
    adjusted = books + additions - deductions
    donation_excess = _amount(rng, MIN_AMOUNT, 3_000_000)
    donation_carryover = _amount(rng, MIN_AMOUNT, 2_000_000)
    income_for_year = adjusted + donation_excess - donation_carryover
    # 과세표준 must stay positive: cap the three deductions well below the income.
    room = max(income_for_year // 8, 3 * MIN_AMOUNT)
    loss_carryforward = _amount(rng, MIN_AMOUNT, room)
    nontaxable = _amount(rng, MIN_AMOUNT, room)
    income_deduction = _amount(rng, MIN_AMOUNT, room)
    taxable = income_for_year - loss_carryforward - nontaxable - income_deduction

    after_credits = _amount(rng, 1_000_000, 60_000_000)
    exempt_credits = _amount(rng, MIN_AMOUNT, max(MIN_AMOUNT + 1_000, after_credits // 2))
    additional = _amount(rng, MIN_AMOUNT, 2_000_000)
    payable = after_credits - exempt_credits + additional

    company = rng.choice(_COMPANIES)
    year = rng.randrange(2022, 2027)
    rows = [
        ("net_income_per_books", "101 결 산 서 상 당 기 순 손 익 01", books, ""),
        ("gross_additions", "102 익 금 산 입 02", additions, ""),
        ("gross_deductions", "103 손 금 산 입 03", deductions, ""),
        ("adjusted_income", "104 차 가 감 소 득 금 액 04", adjusted, " (101＋102－103)"),
        ("donation_excess", "105 기 부 금 한 도 초 과 액 05", donation_excess, ""),
        ("donation_carryover_deduction", "106 기부금한도초과이월액 손금산입 54",
         donation_carryover, ""),
        ("income_for_year", "107 각 사 업 연 도 소 득 금 액 06", income_for_year,
         " (104+105-106)"),
        ("loss_carryforward", "109 이 월 결 손 금 07", loss_carryforward, ""),
        ("nontaxable_income", "110 비 과 세 소 득 08", nontaxable, ""),
        ("income_deduction", "111 소 득 공 제 09", income_deduction, ""),
        ("taxable_income", "112 과 세 표 준 10", taxable, " (108－109－110-111)"),
        ("tax_after_credits", "122 차 감 세 액 18", after_credits, ""),
        ("credits_excluded_from_min_tax", "123 최저한세 적용제외 공제감면세액 19",
         exempt_credits, ""),
        ("additional_tax", "124 가 산 세 액 20", additional, ""),
        ("tax_payable", "125 가 감 계 21", payable, " (122-123+124)"),
    ]
    lines = [
        "법인세 과세표준 및 세액조정계산서",
        "■ 법인세법 시행규칙 [별지 제3호서식]                    (단위: 원)",
        f"법인명 {company}",
        f"사업자등록번호 {_reg_no(rng)}",
        f"사 업 연 도 {year}.01.01 ~ {year}.12.31",
        "① 각 사업연도 소득계산",
    ]
    fields = []
    for name, label, amt, note in rows:
        line = f"{label} {amt:,}{note}"
        lines.append(line)
        fields.append(GenField(name, f"{amt:,}원", amt, line))
    # No detail rows on this form: every printed amount is itself a labeled total,
    # so a field-swap has to take another labeled figure. `_inject_swap` already
    # refuses a source equal to the target's own value.
    swap_sources = [(amt, f"{label} {amt:,}{note}") for _n, label, amt, note in rows]
    return BuiltDoc("corporate_tax_return", "\n".join(lines) + "\n", fields, swap_sources)


# --- error injection -----------------------------------------------------------


def _inject_ungrounded(rng: random.Random, doc: BuiltDoc) -> str:
    """Replace one rule-covered field with a number absent from the document."""
    tgt = rng.choice([f for f in doc.fields if f.field in _RULE_FIELDS[doc.doc_type]])
    taken = {normalize_number_str(t) for t in _NUM_TOKEN.findall(doc.full_text)}
    true_numbers = [f.number for f in doc.fields]
    for _ in range(1_000):
        if rng.random() < 0.5:
            fake = rng.randrange(100, 100_000) * 1_000  # round thousands
        else:
            fake = rng.randrange(100_000, 99_999_999)   # arbitrary amount
        # Absent from every number token AND clear of the 1-won rule tolerance.
        if str(fake) not in taken and all(abs(fake - n) > 10 for n in true_numbers):
            break
    else:  # pragma: no cover - practically unreachable
        raise RuntimeError("could not find a fake amount absent from the document")
    tgt.grounding_quote = tgt.grounding_quote.replace(f"{tgt.number:,}", f"{fake:,}")
    tgt.raw = f"{fake:,}원"
    tgt.number = fake
    tgt.expected = "discarded"
    return tgt.field


def _inject_swap(rng: random.Random, doc: BuiltDoc) -> str:
    """Give one rule-covered field another field's number (grounded, rule-breaking)."""
    tgt = rng.choice([f for f in doc.fields if f.field in _RULE_FIELDS[doc.doc_type]])
    number, line = rng.choice([(n, ln) for n, ln in doc.swap_sources if n != tgt.number])
    tgt.raw = f"{number:,}원"
    tgt.number = number
    tgt.grounding_quote = line  # a real span - the value truly is in the document
    tgt.expected = "discarded"
    return tgt.field


# --- gate self-validation ------------------------------------------------------


def _validate(
    doc: BuiltDoc, injected: str | None, kind: str | None, packs: dict[str, RulePack]
) -> str | None:
    """Run the real gate; return a reason string if the doc breaks a bench invariant."""
    values = [
        ExtractedValue(f.field, f.raw, float(f.number), f.grounding_quote) for f in doc.fields
    ]
    gate_out = {vf.field: vf for vf in run_gate(values, doc.full_text, packs[doc.doc_type])}
    for f in doc.fields:
        vf = gate_out[f.field]
        if f.expected == "discarded" and vf.verdict is not Verdict.DISCARDED:
            return f"bad field {f.field!r} escaped the gate (would break recall)"
        if kind is None and vf.verdict is not Verdict.VERIFIED:
            return f"clean-doc field {f.field!r} was discarded: {vf.failed_checks}"
    if kind == "field-swap" and not gate_out[injected].grounded:
        return f"swap field {injected!r} lost grounding (must be an arithmetic-only catch)"
    if kind == "ungrounded" and gate_out[injected].grounded:
        return f"fake field {injected!r} accidentally grounded"
    return None


def _generate_doc(
    rng: random.Random,
    doc_type: str,
    with_items: bool,
    kind: str | None,
    packs: dict[str, RulePack],
) -> tuple[BuiltDoc, str | None]:
    for _ in range(MAX_DOC_ATTEMPTS):
        if doc_type == "tax_invoice":
            doc = _build_tax(rng, with_items)
        elif doc_type == "statement":
            doc = _build_stmt(rng)
        elif doc_type == "balance_sheet":
            doc = _build_balance(rng)
        elif doc_type == "income_statement":
            doc = _build_income(rng)
        else:
            doc = _build_taxreturn(rng)
        injected: str | None = None
        if kind == "ungrounded":
            injected = _inject_ungrounded(rng, doc)
        elif kind == "field-swap":
            injected = _inject_swap(rng, doc)
        if _validate(doc, injected, kind, packs) is None:
            return doc, injected
    raise RuntimeError(  # pragma: no cover - practically unreachable
        f"could not build a valid {doc_type} doc after {MAX_DOC_ATTEMPTS} attempts"
    )


# --- orchestration -------------------------------------------------------------


def generate(
    count_tax: int,
    count_stmt: int,
    count_balance: int,
    seed: int,
    out_dir: Path,
    count_income: int = 0,
    count_taxreturn: int = 0,
) -> dict:
    """Generate the synthetic golden set; return generation statistics."""
    rng = random.Random(seed)
    packs = {
        doc_type: load_rule_pack(RULES_DIR / f"{doc_type}.yaml") for doc_type in _RULE_FIELDS
    }

    # Every 3rd tax invoice also extracts line items (exercises the sum rule).
    layouts = [("tax_invoice", i % 3 == 0) for i in range(count_tax)]
    layouts += [("statement", False) for _ in range(count_stmt)]
    layouts += [("balance_sheet", False) for _ in range(count_balance)]
    layouts += [("income_statement", False) for _ in range(count_income)]
    layouts += [("corporate_tax_return", False) for _ in range(count_taxreturn)]
    per_doc = {
        "statement": 2,
        "balance_sheet": 6,
        "income_statement": 10,
        "corporate_tax_return": 15,
    }
    total_fields = sum(
        (5 if with_items else 3) if doc_type == "tax_invoice" else per_doc[doc_type]
        for doc_type, with_items in layouts
    )

    # ~20% of fields bad, at most one injection per doc, with a guaranteed
    # handful of field-swap ("only arithmetic catches it") cases.
    n_bad = min(round(BAD_FIELD_RATE * total_fields), int(len(layouts) * MAX_BAD_DOC_SHARE))
    n_swap = round(SWAP_SHARE * n_bad)
    if n_bad >= MIN_SWAPS:
        n_swap = max(n_swap, MIN_SWAPS)
    n_swap = min(n_swap, n_bad)
    kinds = ["field-swap"] * n_swap + ["ungrounded"] * (n_bad - n_swap)
    rng.shuffle(kinds)
    schedule = dict(zip(rng.sample(range(len(layouts)), n_bad), kinds, strict=True))

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(out_dir.glob("gen_*.json")):
        stale.unlink()  # regenerate our own files only; hand-written goldens stay

    prefix = {
        "tax_invoice": "gen_tax",
        "statement": "gen_stmt",
        "balance_sheet": "gen_bs",
        "income_statement": "gen_is",
        "corporate_tax_return": "gen_ctr",
    }
    counters = dict.fromkeys(_RULE_FIELDS, 0)
    written: list[str] = []
    for idx, (doc_type, with_items) in enumerate(layouts):
        kind = schedule.get(idx)
        doc, injected = _generate_doc(rng, doc_type, with_items, kind, packs)
        counters[doc_type] += 1
        doc_id = f"{prefix[doc_type]}_{counters[doc_type]:03d}"
        payload = {
            "doc_id": doc_id,
            "doc_type": doc.doc_type,
            "full_text": doc.full_text,
            "injected_error": {"kind": kind, "field": injected} if kind else None,
            "fields": [f.to_json_obj() for f in doc.fields],
        }
        path = out_dir / f"{doc_id}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        written.append(path.name)

    manual_kept = [p.name for p in sorted(out_dir.glob("*.json")) if not p.name.startswith("gen_")]
    return {
        "seed": seed,
        "out_dir": str(out_dir),
        "docs": len(layouts),
        "tax_docs": count_tax,
        "stmt_docs": count_stmt,
        "balance_docs": count_balance,
        "income_docs": count_income,
        "taxreturn_docs": count_taxreturn,
        "fields": total_fields,
        "bad_fields": n_bad,
        "ungrounded": n_bad - n_swap,
        "field_swap": n_swap,
        "clean_docs": len(layouts) - n_bad,
        "manual_kept": len(manual_kept),
        "written": written,
    }


def _print_stats(stats: dict) -> None:
    fields = stats["fields"]
    rate = stats["bad_fields"] / fields if fields else 0.0
    print("=== NumHall-KR golden generator ===")
    print(
        f"seed={stats['seed']}  tax={stats['tax_docs']}  "
        f"stmt={stats['stmt_docs']}  balance={stats['balance_docs']}  "
        f"income={stats['income_docs']}  taxreturn={stats['taxreturn_docs']}"
    )
    print(f"out: {stats['out_dir']}")
    print(
        f"docs written: {stats['docs']} "
        f"(clean {stats['clean_docs']}, with injected error {stats['bad_fields']})"
    )
    print(f"labeled fields: {fields}")
    print(f"injected bad fields: {stats['bad_fields']}/{fields} ({rate:.1%})")
    print(f"  ungrounded (fake number + fake quote)       : {stats['ungrounded']}")
    print(f"  field-swap (grounded, arithmetic-only catch): {stats['field_swap']}")
    print(f"hand-written goldens kept untouched: {stats['manual_kept']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic NumHall-KR synthetic golden set."
    )
    parser.add_argument("--count-tax", type=int, default=30, help="tax invoices (default 30)")
    parser.add_argument("--count-stmt", type=int, default=10, help="statements (default 10)")
    parser.add_argument(
        "--count-balance", type=int, default=10, help="balance sheets (default 10)"
    )
    parser.add_argument(
        "--count-income", type=int, default=8, help="income statements (default 8)"
    )
    parser.add_argument(
        "--count-taxreturn", type=int, default=6, help="corporate tax returns (default 6)"
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42)")
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="output dir (default bench/golden)"
    )
    args = parser.parse_args(argv)
    stats = generate(
        args.count_tax,
        args.count_stmt,
        args.count_balance,
        args.seed,
        args.out_dir,
        count_income=args.count_income,
        count_taxreturn=args.count_taxreturn,
    )
    _print_stats(stats)


if __name__ == "__main__":
    main()
