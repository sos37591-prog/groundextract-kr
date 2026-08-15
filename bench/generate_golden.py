"""NumHall-KR synthetic golden-set generator (deterministic, key-free).

Writes ``gen_*.json`` golden documents into ``bench/golden/`` alongside the
hand-written goldens (which are never overwritten): Korean tax invoices
(전자세금계산서) and trial-balance statements (합계잔액시산표) with realistic
layouts - 공급가액/세액/합계금액, 차변/대변 합계, varied thousands-comma amounts.

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

Run:  python bench/generate_golden.py [--count-tax 30 --count-stmt 10 --seed 42]
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

BAD_FIELD_RATE = 0.20  # ~20% of all labeled fields carry an injected error
SWAP_SHARE = 0.4       # of the injected errors, this share are field-swaps
MIN_SWAPS = 3          # always include a few arithmetic-only catches
MAX_DOC_ATTEMPTS = 50  # deterministic re-rolls if a candidate fails the gate

# Same number-token pattern the grounding matcher uses (PARTIAL_NUMERIC tier).
_NUM_TOKEN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Fields the rule packs reference - injection targets are chosen among these.
_RULE_FIELDS = {
    "tax_invoice": ("supply", "vat", "total"),
    "statement": ("debit_total", "credit_total"),
}

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
        doc = _build_tax(rng, with_items) if doc_type == "tax_invoice" else _build_stmt(rng)
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


def generate(count_tax: int, count_stmt: int, seed: int, out_dir: Path) -> dict:
    """Generate the synthetic golden set; return generation statistics."""
    rng = random.Random(seed)
    packs = {
        "tax_invoice": load_rule_pack(RULES_DIR / "tax_invoice.yaml"),
        "statement": load_rule_pack(RULES_DIR / "statement.yaml"),
    }

    # Every 3rd tax invoice also extracts line items (exercises the sum rule).
    layouts = [("tax_invoice", i % 3 == 0) for i in range(count_tax)]
    layouts += [("statement", False) for _ in range(count_stmt)]
    total_fields = sum(
        (5 if with_items else 3) if doc_type == "tax_invoice" else 2
        for doc_type, with_items in layouts
    )

    # ~20% of fields bad, at most one injection per doc, with a guaranteed
    # handful of field-swap ("only arithmetic catches it") cases.
    n_bad = min(round(BAD_FIELD_RATE * total_fields), len(layouts))
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

    prefix = {"tax_invoice": "gen_tax", "statement": "gen_stmt"}
    counters = {"tax_invoice": 0, "statement": 0}
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
    print(f"seed={stats['seed']}  tax={stats['tax_docs']}  stmt={stats['stmt_docs']}")
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
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42)")
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="output dir (default bench/golden)"
    )
    args = parser.parse_args(argv)
    stats = generate(args.count_tax, args.count_stmt, args.seed, args.out_dir)
    _print_stats(stats)


if __name__ == "__main__":
    main()
