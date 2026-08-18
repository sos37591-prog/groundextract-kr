"""Fault localization: blame the fields that explain a violation, not all of them.

A broken invariant names several fields and indicts only some. Discarding all of
them was the deliberate price of recall — and most of that price is refundable,
because rule packs overlap: a field another rule still corroborates cannot be the
one that broke this one.

Two properties are pinned here, and the second is the one that matters:

1. **Precision.** A field a passing rule vouches for, or that cannot account for
   every violation on its own, survives.
2. **Recall is untouched.** A single wrong value appears in *every* rule that
   broke, so ``{that value}`` is itself a minimum explanation and is always among
   the sets blamed. The union of the minimum explanations therefore cannot drop
   it — which is why ambiguity widens the blame rather than narrowing it.

The security boundary has its own test: a rule downgraded for leaning on
ungrounded values (:func:`groundextract.gate._vouching_check`) verified nothing,
so it must never exonerate anybody. Treating it as a violation to localize would
hand a field exactly the verdict the downgrade exists to deny.
"""

from __future__ import annotations

from pathlib import Path

from groundextract import ExtractedValue, Verdict, load_pack, run_gate
from groundextract.bench import load_golden_dir, run_bench
from groundextract.gate import _localize_fault

ROOT = Path(__file__).resolve().parent.parent

# 공급가액 = 품목1 + 품목2 holds here, so the item rule can vouch for supply.
DOC_WITH_ITEMS = (
    "전자세금계산서\n"
    "품목1 공급가  400,000원\n"
    "품목2 공급가  600,000원\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)

# No line items: nothing independently corroborates 공급가액.
DOC_NO_ITEMS = (
    "전자세금계산서\n"
    "품목 단가  250,000원\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)


def _gate(values, doc):
    return {f.field: f for f in run_gate(values, doc, load_pack("tax_invoice"))}


def _swapped_vat(with_items: bool) -> list[ExtractedValue]:
    """vat carries 250,000 — a real figure from the wrong line."""
    rows = [
        ("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
        ("vat", "250,000원", 250_000, "품목 단가  250,000원"),
        ("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
    ]
    if with_items:
        rows[1] = ("vat", "250,000원", 250_000, None)
        rows += [
            ("item1_supply", "400,000원", 400_000, "품목1 공급가  400,000원"),
            ("item2_supply", "600,000원", 600_000, "품목2 공급가  600,000원"),
        ]
    return [ExtractedValue(f, raw, n, q) for f, raw, n, q in rows]


# --- 1) precision: overlapping invariants narrow the blame ---------------------


def test_a_corroborating_rule_clears_its_fields():
    # 공급가액 = 품목1 + 품목2 passes, so supply cannot be what broke the other two
    # rules. That leaves vat as the only explanation, and nothing else is lost.
    fields = _gate(_swapped_vat(with_items=True), DOC_WITH_ITEMS)
    assert fields["vat"].verdict is Verdict.DISCARDED
    for spared in ("supply", "total", "item1_supply", "item2_supply"):
        assert fields[spared].verdict is Verdict.VERIFIED, spared


def test_ambiguity_blames_every_equally_good_explanation():
    # Without line items, vat and supply each appear in both broken rules, so
    # either alone explains the failures. Both go.
    fields = _gate(_swapped_vat(with_items=False), DOC_NO_ITEMS)
    assert fields["vat"].verdict is Verdict.DISCARDED
    assert fields["supply"].verdict is Verdict.DISCARDED


def test_a_field_that_cannot_explain_every_violation_is_spared():
    # total appears in only one of the two broken rules, so it cannot be the
    # single thing that went wrong.
    fields = _gate(_swapped_vat(with_items=False), DOC_NO_ITEMS)
    assert fields["total"].verdict is Verdict.VERIFIED
    assert any(
        c.name == "fault_localization" and "not implicated" in c.detail
        for c in fields["total"].checks
    )


def test_an_exonerating_note_names_who_is_blamed():
    fields = _gate(_swapped_vat(with_items=True), DOC_WITH_ITEMS)
    note = next(c for c in fields["total"].checks if c.name == "fault_localization")
    assert note.passed  # informational: it must not fail the field
    assert "vat" in note.detail


# --- 2) recall: the culprit is never among the exonerated ----------------------


def test_the_benchmark_keeps_perfect_recall():
    # The property that makes localization safe to ship: not one labeled-bad
    # field escapes. Precision may move; this may not.
    outcomes = run_bench(load_golden_dir(ROOT / "bench" / "golden"), ROOT / "rules")
    escaped = [
        o for o in outcomes if o.expected is Verdict.DISCARDED and o.predicted is Verdict.VERIFIED
    ]
    assert escaped == []


def test_a_single_culprit_is_always_inside_the_blamed_set():
    # Restated as the search itself: whatever the failing rules are, a field
    # present in all of them is a minimum explanation, so it is always blamed.
    failing = [{"vat", "supply"}, {"total", "supply", "vat"}]
    blamed = _localize_fault(failing, cleared=set())
    assert {"vat", "supply"} <= blamed
    blamed_cleared = _localize_fault(failing, cleared={"supply"})
    assert "vat" in blamed_cleared


def test_localization_gives_up_rather_than_guess():
    # Nothing to blame once every candidate is cleared -> None, and run_gate
    # falls back to blaming every field the violations reference.
    assert _localize_fault([{"a", "b"}], cleared={"a", "b"}) is None
    assert _localize_fault([], cleared=set()) is None


def test_the_search_is_bounded():
    # A pathological input must not turn the combinatorial search loose; past the
    # ceiling it declines, which falls back to the conservative blame-everything.
    wide = [{f"f{i}" for i in range(40)}]
    assert _localize_fault(wide, cleared=set()) is None


# --- 3) the security boundary: a downgraded rule exonerates nobody -------------


def test_a_rule_downgraded_for_leaning_on_ungrounded_values_never_exonerates():
    # supply and vat are absent from this document; total happens to appear.
    # total == supply + vat balances, so the rule "passes" and is downgraded for
    # leaning on ungrounded values. Localizing that downgrade would clear total —
    # handing it precisely the verdict the downgrade exists to deny.
    doc = "전자세금계산서\n공급가액  11,000,000원\n세액  1,100,000원\n합계금액  12,100,000원\n"
    fields = _gate(
        [
            ExtractedValue("supply", "1,000,000원", 1_000_000),
            ExtractedValue("vat", "100,000원", 100_000),
            ExtractedValue("total", "1,100,000원", 1_100_000),
        ],
        doc,
    )
    assert fields["total"].grounded  # the figure really is on the page
    assert fields["total"].verdict is Verdict.DISCARDED
    assert not any(c.name == "fault_localization" for c in fields["total"].checks)


# --- 4) exoneration is only sound for a single fault ---------------------------


# 당기/전기 two-column 재무상태표 with the two 총계 rows read from the prior-year
# column — the archetypal OCR failure on this form, and one no attacker is needed
# to produce.
COLUMN_SLIP_DOC = (
    "재무상태표\n"
    "과 목  당기  전기\n"
    "유동자산 600,000,000 500,000,000\n"
    "비유동자산 400,000,000 350,000,000\n"
    "자산총계 1,000,000,000 850,000,000\n"
    "부채총계 300,000,000 250,000,000\n"
    "자본총계 700,000,000 600,000,000\n"
    "부채와자본총계 1,000,000,000 850,000,000\n"
)

COLUMN_SLIP_VALUES = [
    ("current_assets", "600,000,000"),
    ("noncurrent_assets", "400,000,000"),
    ("total_assets", "850,000,000"),  # slipped
    ("total_liabilities", "300,000,000"),
    ("total_equity", "700,000,000"),
    ("total_liab_equity", "850,000,000"),  # slipped
]


def test_two_slipped_totals_are_not_certified_over_the_correct_values():
    """The inversion this restriction exists to prevent.

    Both slipped totals are 850,000,000, so `assets == liab_equity` *passes* and
    clears them; the smallest set explaining the two violations that remain is
    then one correct field from either side. The gate used to verify the two
    misread totals at confidence 1.0 and discard all four correct values — not a
    bypass but its inverse, certifying exactly the numbers a human would catch.
    """
    fields = {
        f.field: f
        for f in run_gate(
            [ExtractedValue(name, raw, None, None) for name, raw in COLUMN_SLIP_VALUES],
            COLUMN_SLIP_DOC,
            load_pack("balance_sheet"),
        )
    }
    for slipped in ("total_assets", "total_liab_equity"):
        assert fields[slipped].verdict is Verdict.DISCARDED, f"{slipped} was certified"
        assert fields[slipped].confidence == 0.0


def test_localization_declines_when_no_single_field_explains_everything():
    # Two disjoint violations need two culprits, and needing two *is* the signal
    # that more than one value is wrong — exactly where exoneration stops being
    # sound. Declining costs precision; guessing costs the guarantee.
    assert _localize_fault([{"a", "b"}, {"c", "d"}], cleared=set()) is None
    # ...while one field covering every violation is still localized.
    assert _localize_fault([{"a", "b"}, {"a", "c"}], cleared=set()) == {"a"}
