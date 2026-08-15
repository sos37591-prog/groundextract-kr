"""Balance-sheet (재무상태표) rule tests.

The headline differentiation: a single misread figure that is *self-consistent*
with the OCR text (so grounding passes) still gets caught — because it breaks a
cross-field arithmetic invariant that neither OCR nor a human eye checks.
"""

from __future__ import annotations

from pathlib import Path

from groundextract import ExtractedValue, Verdict, load_rule_pack, run_gate

ROOT = Path(__file__).resolve().parent.parent
PACK = load_rule_pack(ROOT / "rules" / "balance_sheet.yaml")

# Figures match viewer/samples/balance_sheet_sample.pdf (synthetic).
# 유동 398,180,000 + 비유동 39,431,995 = 자산총계 437,611,995 = 부채와자본총계
# 부채 109,500,000 + 자본 328,111,995 = 437,611,995   (all invariants hold)
CLEAN = {
    "current_assets": 398_180_000,
    "noncurrent_assets": 39_431_995,
    "total_assets": 437_611_995,
    "total_liabilities": 109_500_000,
    "total_equity": 328_111_995,
    "total_liab_equity": 437_611_995,
}
DOC = " ".join(f"{v:,}" for v in CLEAN.values())  # grounding text (all figures present)


def _by_field(overrides: dict[str, int], text: str):
    vals = [
        ExtractedValue(k, f"{overrides.get(k, v):,}원", float(overrides.get(k, v)))
        for k, v in CLEAN.items()
    ]
    return {f.field: f for f in run_gate(vals, text, PACK)}


def test_clean_balance_all_verified():
    fields = _by_field({}, DOC)
    assert all(f.verdict is Verdict.VERIFIED for f in fields.values())


def test_single_misread_caught_by_arithmetic_only():
    # 비유동자산 misread by one digit (39,431,995 -> 39,431,895). The misread is
    # present in the (OCR) text, so grounding PASSES; only the sum rule fails.
    text = DOC.replace("39,431,995", "39,431,895")
    fields = _by_field({"noncurrent_assets": 39_431_895}, text)

    assert fields["noncurrent_assets"].grounded is True  # OCR is self-consistent
    # asset side breaks 자산총계 = 유동 + 비유동 -> all three discarded
    for f in ("noncurrent_assets", "current_assets", "total_assets"):
        assert fields[f].verdict is Verdict.DISCARDED, f
        assert fields[f].confidence == 0.0
    # the arithmetic check (not grounding) is what fired on the misread field
    assert any(
        c.name == "assets_eq_current_plus_noncurrent" and not c.passed
        for c in fields["noncurrent_assets"].checks
    )
    # 부채·자본 side is untouched
    for f in ("total_liabilities", "total_equity", "total_liab_equity"):
        assert fields[f].verdict is Verdict.VERIFIED, f
