"""Gate-bypass regressions: ways a fabricated extraction used to pass.

Every test here is a *verified* verdict the gate once handed to a value the
document does not support. They share one root cause — two halves of the gate
being allowed to reason about different things — and each pins the half that
closes it:

1. **Fabricated quote.** A cited span was judged by the same tiered matcher used
   for values, so a quote carrying two numbers was accepted as real when only
   the *first* one existed. Staple a real figure onto an invented one and the
   invented half counted as verbatim evidence.
2. **``raw`` vs ``number``.** Grounding checked ``raw`` while the rule engine did
   its arithmetic on the caller-supplied ``number``. Quote the document exactly,
   invent the number, and both halves went green on different figures.
3. **Substring digits.** ``EXACT`` was a substring test, and "1,000,000원" is a
   substring of "11,000,000원" — the 10x shift is the dominant OCR/LLM digit
   error, so the single most likely corruption was also the one that passed.
4. **Duplicate field names.** Arithmetic runs over a field-name environment, so a
   repeated name let one instance supply the number while every instance
   inherited the resulting check.
5. **Vouching with ungrounded values.** An invariant holding across the
   extractor's own numbers proves nothing unless those numbers came from the
   document; a self-consistent invention could verify the one figure that did
   ground.

6. **Notation read as quantity.** ``normalize_number_str`` kept only the first
   number token, so "1 234 567원" reduced to the key "1" — and NFKC turns the
   line-item markers ①②③ into digits, so the document offered a "1" to ground
   it against.
7. **Unbounded work.** The fuzzy tier walks the source for every value, so one
   well-formed request could occupy the (single-threaded) MCP server for
   minutes, starving every other caller on the pipe. Three caps failed here
   before the fourth worked, and each failure was the same mistake in a new
   place: bounding something other than the work. Counting source *characters*
   missed that cost is not linear in them. Predicting cost as source tokens x
   value tokens tracked it far too weakly, leaving shapes that cost tens of
   seconds inside a few percent of their allowance. Charging the real comparison
   size but pricing only ``len(value) x len(span)`` billed a two-character value
   two units a call, making *short* values the expensive family. And bounding
   each value bounded nothing about a request carrying ``MAX_VALUES`` of them.
   The walk now charges every comparison, including a per-call floor, against
   both a per-value and a per-request budget. ``extract_verified`` separately
   enforced no document cap at all.
8. **Fragments as evidence.** A textual value grounded against any slice of the
   page, so a truncated 상호 or identifier counted as verbatim.
9. **Spelling deciding the checks.** The gate asked ``normalize_number_str``
   whether a value was a number, and that returns ``None`` both for text and for
   a string holding several tokens. So "1 000 000원" — an ordinary OCR spelling
   of a Korean invoice figure — was filed as text, the arithmetic requirement
   fell away, and a misassignment that is caught when spelled "1,000,000원" came
   back verified at confidence 1.0. Widening the predicate to "holds digits"
   closed that, and left the same hole for quantities written without digits at
   all — 한글/한자 수사, Roman numerals, superscripts. What the gate promises
   about a field its rule pack *names* cannot depend on notation, so a named
   field with no readable number now fails closed however it is spelled.

Interleaved throughout are the counterweights: a faithful extraction must still
verify and legitimate formatting must still ground, or these fixes would have
been bought with a gate that trusts nothing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from groundextract import (
    ExtractedValue,
    MatchKind,
    Verdict,
    ground_value,
    load_rule_pack,
    match_value,
    run_gate,
)
from groundextract.grounding import (
    MAX_FUZZY_REQUEST_CHARS,
    MAX_FUZZY_SOURCE_CHARS,
    FuzzyBudget,
    normalize_number_str,
)
from groundextract.mcp_server import (
    MAX_FULL_TEXT_CHARS,
    MAX_TIMEOUT_SECONDS,
    MAX_VALUE_CHARS,
    MAX_VALUES,
    InvalidParamsError,
    _extractor_kwargs,
    _parse_values,
    _tool_extract_verified,
    _tool_verify_extraction,
)

RULES = Path(__file__).resolve().parent.parent / "rules" / "tax_invoice.yaml"

# 1,000,000 / 100,000 / 1,100,000 — nothing else appears in this document.
DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)

# The same invoice an order of magnitude larger. Used for the substring test:
# every figure in DOC is a substring of the corresponding figure here.
DOC_10X = (
    "전자세금계산서\n"
    "공급가액  11,000,000원\n"
    "세액        1,100,000원\n"
    "합계금액  12,100,000원\n"
)


def _fields(values, doc=DOC):
    return {f"{f.field}:{f.value.raw}": f for f in run_gate(values, doc, load_rule_pack(RULES))}


def _all_discarded(fields):
    return all(
        f.verdict is Verdict.DISCARDED and f.confidence == 0.0 for f in fields.values()
    )


# --- 1) a cited span cannot launder an invented amount -------------------------


def test_fabricated_quote_cannot_launder_an_invented_amount():
    # Each quote pairs a figure that really is in the document with one that is
    # not. The invented triple is internally consistent (200,000 == 2,000,000 *
    # 0.1), so arithmetic alone would wave it through.
    values = [
        ExtractedValue("supply", "2,000,000원", 2_000_000, "1,000,000 공급가액 2,000,000원"),
        ExtractedValue("vat", "200,000원", 200_000, "1,000,000 세액 200,000원"),
        ExtractedValue("total", "2,200,000원", 2_200_000, "1,000,000 합계금액 2,200,000원"),
    ]
    fields = _fields(values)
    assert _all_discarded(fields)
    # rejected for the right reason: the cited span is not in the document
    detail = fields["supply:2,000,000원"].checks[0].detail
    assert "not found verbatim" in detail


def test_a_real_quote_cannot_ground_a_value_the_document_lacks():
    # The quote itself is genuine this time; only the value is invented. The
    # value must stand against the whole document, not just the cited span.
    check = ground_value("9,999,999원", "공급가액  1,000,000원", DOC)
    assert not check.passed
    assert check.kind is MatchKind.NONE


# --- 2) a supplied number must agree with the string that was grounded ---------


def test_supplied_number_must_agree_with_raw():
    # raw quotes the document exactly, so grounding passes; only `number` lies.
    # Arithmetic over the invented numbers balances perfectly.
    values = [
        ExtractedValue("supply", "11,000,000원", 1_100_000, "공급가액  11,000,000원"),
        ExtractedValue("vat", "1,100,000원", 110_000, "세액        1,100,000원"),
        ExtractedValue("total", "12,100,000원", 1_210_000, "합계금액  12,100,000원"),
    ]
    fields = _fields(values, DOC_10X)
    assert _all_discarded(fields)
    disagreement = [
        c
        for c in fields["supply:11,000,000원"].checks
        if c.name == "raw_number_agreement"
    ]
    assert disagreement and not disagreement[0].passed


def test_a_number_supplied_for_a_textual_raw_is_rejected():
    # Nothing grounds a number that its own raw string does not contain.
    values = [ExtractedValue("supplier_name", "주식회사 가나다", 1_000_000, None)]
    doc = "상호 주식회사 가나다\n"
    fields = _fields(values, doc)
    assert _all_discarded(fields)


def test_an_agreeing_number_still_passes():
    values = [
        ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
        ExtractedValue("vat", "100,000원", 100_000, "세액        100,000원"),
        ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
    ]
    fields = _fields(values)
    assert all(f.verdict is Verdict.VERIFIED for f in fields.values())


# --- 3) a shorter amount is not evidence for a longer one ----------------------


def test_shorter_amount_is_not_grounded_by_a_longer_one():
    # The substring relation that used to report this as verbatim evidence.
    assert "1,000,000원" in "11,000,000원"
    kind, _ = match_value("1,000,000원", "공급가액  11,000,000원")
    assert kind is MatchKind.NONE


def test_ten_times_shifted_invoice_is_fully_discarded():
    # Every value is a substring of the corresponding figure in DOC_10X, and the
    # shifted triple satisfies both arithmetic rules among themselves.
    values = [
        ExtractedValue("supply", "1,000,000원", 1_000_000),
        ExtractedValue("vat", "100,000원", 100_000),
        ExtractedValue("total", "1,100,000원", 1_100_000),
    ]
    fields = _fields(values, DOC_10X)
    assert _all_discarded(fields)


def test_formatting_differences_still_ground():
    # The boundary fix must not cost the PARTIAL_NUMERIC tier its job.
    kind, _ = match_value("1000000", "공급가액  1,000,000원")
    assert kind is MatchKind.PARTIAL_NUMERIC


# --- 4) a repeated field name is ambiguous, and ambiguity fails closed ---------


def test_duplicate_field_is_discarded_on_both_sides():
    # 2,000,000 really is in this document (as a line-item unit price), so the
    # decoy grounds. The genuine supply supplies the number the rules balance.
    doc = (
        "전자세금계산서\n"
        "품목 단가  2,000,000원\n"
        "공급가액  1,000,000원\n"
        "세액        100,000원\n"
        "합계금액  1,100,000원\n"
    )
    values = [
        ExtractedValue("supply", "2,000,000원", 2_000_000, "품목 단가  2,000,000원"),
        ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
        ExtractedValue("vat", "100,000원", 100_000, "세액        100,000원"),
        ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
    ]
    fields = _fields(values, doc)
    # both instances are discarded, and so are the siblings whose rules can no
    # longer be evaluated without an unambiguous `supply`
    assert _all_discarded(fields)
    dupes = [
        c for c in fields["supply:2,000,000원"].checks if c.name == "duplicate_field"
    ]
    assert dupes and not dupes[0].passed


# --- 5) a rule may not vouch with numbers the document never supported ---------


def test_a_rule_cannot_vouch_using_an_ungrounded_sibling():
    # supply and vat are absent from DOC_10X; total (1,100,000) happens to
    # appear there as the VAT line, so it grounds on its own. The invariant
    # total == supply + vat then balances — but only by leaning on two figures
    # the document never contained, so it must not verify `total`.
    values = [
        ExtractedValue("supply", "1,000,000원", 1_000_000),
        ExtractedValue("vat", "100,000원", 100_000),
        ExtractedValue("total", "1,100,000원", 1_100_000),
    ]
    fields = _fields(values, DOC_10X)
    total = fields["total:1,100,000원"]
    assert total.grounded  # the figure really is on the page...
    assert total.verdict is Verdict.DISCARDED  # ...but nothing verified it
    leaning = [c for c in total.checks if "leans on" in c.detail]
    assert leaning and not leaning[0].passed


def test_a_violated_rule_still_reports_the_violation():
    # Downgrading only applies to *passing* checks: a hallucinated VAT must
    # still surface as the arithmetic violation it is, not as "no rule applied".
    values = [
        ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
        ExtractedValue("vat", "999,999원", 999_999),
        ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
    ]
    fields = _fields(values)
    violated = [
        c
        for c in fields["vat:999,999원"].checks
        if c.name == "vat_equals_supply_x_10pct"
    ]
    assert violated and not violated[0].passed
    assert "999,999" in violated[0].detail


# --- 6) grounding must not invent evidence out of notation ---------------------


def test_space_separated_thousands_have_no_single_reading():
    # "1 234 567" used to reduce to the key "1" (first token only), which then
    # matched any stray 1 in the document.
    assert normalize_number_str("1 234 567원") is None
    assert normalize_number_str("1\xa0000\xa0000") is None  # NBSP separators
    # ...and it is not quietly handed to the fuzzy tier either
    kind, _ = match_value("1 234 567원", "공급가액  1,000,000원")
    assert kind is MatchKind.NONE


def test_enclosed_item_numbers_are_not_amounts():
    # NFKC rewrites ①②③ to 1/2/3. Korean forms number their line items that
    # way, so the document would otherwise offer grounding for a "1" it never
    # stated as a figure.
    assert normalize_number_str("①") is None
    kind, _ = match_value("1", "① 매출\n② 매입\n③ 합계\n")
    assert kind is MatchKind.NONE


def test_full_width_digits_still_ground():
    # ...while genuinely wide digits (category Nd, not No) must keep working.
    kind, _ = match_value("1000000", "공급가액  １，０００，０００원")
    assert kind is MatchKind.PARTIAL_NUMERIC


def test_single_number_readings_are_unchanged():
    assert normalize_number_str("1,000,000원") == "1000000"
    assert normalize_number_str("₩ 1,000,000.00") == "1000000"
    assert normalize_number_str("△1,234") == "-1234"
    assert normalize_number_str("(1,234)") == "-1234"
    assert normalize_number_str("10%") == "10"


# --- 7) one well-formed request cannot monopolise the server -------------------


def test_fuzzy_tier_declines_an_oversized_source():
    # The sliding window is the only unbounded path; past the cap it declines,
    # which is the fail-closed direction (a discard, never a false verify).
    huge = "가나다라마바사아자차카타파하" * (MAX_FUZZY_SOURCE_CHARS // 10)
    kind, detail = match_value("주식회사 가나다", huge)
    assert kind is MatchKind.NONE
    assert "too large for fuzzy matching" in detail


def test_worst_case_request_stays_fast():
    # MCP caps a request at MAX_VALUES fields over MAX_FULL_TEXT_CHARS of text.
    # That shape used to cost minutes; it must now be a fraction of a second.
    #
    # This source is *past* MAX_FUZZY_SOURCE_CHARS, so it is declined before the
    # walk starts and measures only the size check. Kept because that path has to
    # stay cheap too, but it is not the worst case — see below for that.
    source = "가나다라마바사아자차카타파하" * (MAX_FULL_TEXT_CHARS // 13)
    started = time.perf_counter()
    for i in range(MAX_VALUES):
        match_value(f"주식회사 가나다{i}", source)
    assert time.perf_counter() - started < 5.0


def _hostile_source(token_chars: int) -> str:
    token = "가" * token_chars
    source = " ".join([token] * (MAX_FUZZY_SOURCE_CHARS // (token_chars + 1)))
    return source[:MAX_FUZZY_SOURCE_CHARS]


def _never_matching_value(length: int, tokens: int) -> str:
    piece = "나" * max(length // tokens, 1)
    return (" ".join([piece] * tokens))[:length]


# Shapes that sit *inside* every declared cap and still make the span walk as
# expensive as it can be. Both earlier versions of this test pinned a single
# point — a 1024-character, single-token value — and both times the guard they
# were protecting was broken somewhere else in the family: that value is the
# *cheapest* point once cost is charged per comparison, because one long string
# drains the budget in a few hundred calls. The expensive shapes are short values
# against a finely tokenized source, which buy tens of thousands of calls.
#
# So sweep the value axis too. A test that samples one shape and calls it the
# worst is how this defence got shipped broken twice.
@pytest.mark.parametrize("source_token_chars", [1, 2, 4, 16, 64])
@pytest.mark.parametrize(("value_chars", "value_tokens"), [(2, 1), (16, 16), (64, 4), (1024, 1)])
def test_the_span_walk_is_bounded_whatever_the_shape(
    source_token_chars, value_chars, value_tokens
):
    source = _hostile_source(source_token_chars)
    value = _never_matching_value(value_chars, value_tokens)

    started = time.perf_counter()
    kind, _ = match_value(value, source)
    elapsed = time.perf_counter() - started

    assert kind is MatchKind.NONE  # fail-closed: never a false verify
    assert elapsed < 2.0, f"{elapsed:.3f}s for one value"


def test_a_full_request_is_bounded_not_just_each_value_in_it():
    """The bound that actually protects the server.

    Per-value budgets bound a value. A caller sends `MAX_VALUES` of them, so
    bounding each in isolation bounded nothing about the request — every value
    sat inside its own allowance and the total still ran into minutes on a loop
    that answers one caller at a time. `run_gate` now opens one `FuzzyBudget` and
    every value spends from it.
    """
    source = _hostile_source(1)
    values = [
        ExtractedValue(f"f{i}", _never_matching_value(16, 16) + "다" * (i % 3), None, None)
        for i in range(MAX_VALUES)
    ]

    started = time.perf_counter()
    fields = run_gate(values, source, load_rule_pack(RULES))
    elapsed = time.perf_counter() - started

    assert len(fields) == MAX_VALUES
    assert all(f.verdict is Verdict.DISCARDED for f in fields)
    assert elapsed < 10.0, f"{elapsed:.2f}s for a full {MAX_VALUES}-value request"


@pytest.mark.parametrize("doc_chars", [2048, 16384, 32768, 49152, MAX_FUZZY_SOURCE_CHARS])
def test_ocr_noise_still_grounds_at_every_document_size(doc_chars):
    """The regression a budget invites: a value plainly on the page, ungrounded.

    Once the walk can stop early, the order it visits span widths in stops being
    a matter of speed and starts deciding correctness. Sweeping ascending spent
    the entire allowance comparing a two-token 상호 against single tokens, so a
    document past ~49 KiB ran out before reaching the width the match actually
    lived at — and the 상호 was right there in the first line. The value's own
    token count is tried first now.
    """
    line = (
        "전자세금계산서 공급자 주식회사 가온소프트 "
        "공급받는자 주식회사 나래상사 품목 웹서비스 유지보수 "
    )
    doc = (line * (doc_chars // len(line) + 1))[:doc_chars]
    kind, detail = match_value("주식회사 가온소프", doc)  # one character dropped
    assert kind is MatchKind.FUZZY, f"{doc_chars} chars: {detail}"


def test_running_out_of_budget_does_not_read_as_the_document_lacking_the_value():
    # Fail-closed is right; saying the wrong thing about *why* is not. "no
    # grounding" reads as "the document does not say this", and a caller acting
    # on that goes hunting a hallucination that was never there.
    tail = ("가나다 라마바 사아자 " * 3000)[:60000] + " 공급자 주식회사 특이상사 "
    kind, detail = match_value("주식회사 특이상", tail)
    assert kind is MatchKind.NONE
    assert "cut short" in detail
    assert "may still be in the document" in detail

    # ...while a value genuinely absent still says so plainly.
    line = "전자세금계산서 공급자 주식회사 가온소프트 "
    kind, detail = match_value("주식회사 없는회사", (line * 200)[:8192])
    assert kind is MatchKind.NONE
    assert "cut short" not in detail


def test_exact_matches_do_not_touch_the_shared_budget():
    # The common case must not compete for it. A value that occurs verbatim
    # returns before the fuzzy tier, so a hundred honest fields spend nothing and
    # cannot starve each other.
    line = "전자세금계산서 공급자 주식회사 가온소프트 공급받는자 주식회사 나래상사 "
    doc = (line * (MAX_FUZZY_SOURCE_CHARS // len(line) + 1))[:MAX_FUZZY_SOURCE_CHARS]
    budget = FuzzyBudget()
    for i in range(100):
        ground_value("주식회사 가온소프트" if i % 2 else "주식회사 나래상사", None, doc, budget)
    assert budget.remaining == MAX_FUZZY_REQUEST_CHARS


def test_an_honest_request_is_nowhere_near_the_request_budget():
    # The counterweight: the shared budget must not starve ordinary work. A real
    # document with a real 상호 has to ground on the first value and cost nothing
    # worth measuring.
    line = "전자세금계산서 공급자 주식회사 가온소프트 공급받는자 주식회사 나래상사 "
    honest = (line * (MAX_FUZZY_SOURCE_CHARS // len(line) + 1))[:MAX_FUZZY_SOURCE_CHARS]
    started = time.perf_counter()
    fields = run_gate(
        [ExtractedValue("supplier_name", "주식회사 가온소프트", None, None)],
        honest,
        load_rule_pack(RULES),
    )
    assert fields[0].verdict is Verdict.VERIFIED
    assert time.perf_counter() - started < 1.0


def test_a_realistic_document_still_grounds_after_the_budget():
    # The counterweight: the budget must not be so tight that ordinary Korean
    # documents start failing to ground. Exact, fuzzy-with-OCR-noise, and a
    # genuine miss all have to keep behaving.
    source = "공급자 주식회사 가온소프트 서울특별시 강남구 " * 400
    assert match_value("주식회사 가온소프트", source)[0] is MatchKind.EXACT
    assert match_value("주식회사 가온소프", source)[0] is MatchKind.FUZZY
    assert match_value("주식회사 없는회사", source)[0] is MatchKind.NONE


def test_mcp_rejects_oversized_requests():
    for args, message in [
        ({"values": [{"field": "a", "raw": "1"}] * (MAX_VALUES + 1)}, "more than"),
        ({"values": [{"field": "a", "raw": "1" * (MAX_VALUE_CHARS + 1)}]}, "exceeds"),
        ({"values": [{"field": "a" * (MAX_VALUE_CHARS + 1), "raw": "1"}]}, "exceeds"),
        (
            {"values": [{"field": "a", "raw": "1", "grounding_quote": "q" * 2000}]},
            "exceeds",
        ),
    ]:
        with pytest.raises(InvalidParamsError, match=message):
            _parse_values(args["values"])


@pytest.mark.parametrize("tool", [_tool_verify_extraction, _tool_extract_verified])
def test_mcp_rejects_an_oversized_document(tool):
    # Both tools, not just the cheap one. extract_verified took an unbounded
    # string, so a 4 MiB document (MAX_LINE_CHARS) went into the prompt, over the
    # wire, and back through the gate — strictly more work than the tool that
    # *did* enforce the cap.
    with pytest.raises(InvalidParamsError, match="exceeds"):
        tool(
            {
                "full_text": "가" * (MAX_FULL_TEXT_CHARS + 1),
                "doc_type": "tax_invoice",
                "values": [],
            }
        )


def test_mcp_rejects_an_unbounded_timeout():
    # The stdio loop is sequential, so a caller-chosen timeout is how long one
    # request may keep every other caller waiting.
    with pytest.raises(InvalidParamsError, match="exceeds"):
        _extractor_kwargs({"timeout": MAX_TIMEOUT_SECONDS + 1})
    assert _extractor_kwargs({"timeout": 30})["timeout"] == 30.0


# --- 8) a textual value must be a whole token, not any slice of the page ------


DOC_TEXT = "상호 주식회사 지티소프트\n품목 웹서비스 유지보수(8월)\n"


def test_a_fragment_of_the_page_does_not_ground():
    # 상호·품목 carry no arithmetic invariant, so grounding is their *only*
    # check. A substring test made every fragment of the document pass: against
    # "상호 주식회사 지티소프트" even "주" came back verified at confidence 1.0.
    for fragment in ["주식회사 지티", "주식회사 지", "주", "웹서비스 유지보", "웹서비스 유지"]:
        kind, _ = match_value(fragment, DOC_TEXT)
        assert kind is MatchKind.NONE, fragment


def test_a_near_miss_is_never_reported_as_verbatim():
    # The tier matters as much as the verdict. A value the document does not
    # literally contain must not come back EXACT ("verbatim evidence") — at most
    # FUZZY, which says on its face that this is a tolerance, not a quotation.
    for near in ["주식회사 지티소프", "주식회사 지티소프ㅌ"]:
        kind, _ = match_value(near, DOC_TEXT)
        assert kind is not MatchKind.EXACT, near


def test_ocr_tolerance_is_symmetric_between_slips_and_truncation():
    # Scoring against token-aligned spans (rather than character windows sized
    # from the value) is what makes the tier coherent: it used to accept the
    # six-character truncation "주식회사 지티" at 0.88 while rejecting the far
    # likelier one-character slip "주식회사 지티소프" at 0.80.
    assert match_value("주식회사 지티소프", DOC_TEXT)[0] is MatchKind.FUZZY  # 1 dropped
    assert match_value("주식회사 지티소프ㅌ", DOC_TEXT)[0] is MatchKind.FUZZY  # 1 misread
    assert match_value("주식회사 지티", DOC_TEXT)[0] is MatchKind.NONE  # too far gone
    assert match_value("주식회사 삼성전자", DOC_TEXT)[0] is MatchKind.NONE  # different firm


def test_whole_tokens_still_ground():
    for whole in ["주식회사 지티소프트", "웹서비스 유지보수", "유지보수", "주식회사"]:
        kind, _ = match_value(whole, DOC_TEXT)
        assert kind is MatchKind.EXACT, whole


def test_punctuation_counts_as_a_boundary():
    # Korean does not space its syllables but does abut punctuation, so the
    # boundary cannot be "surrounded by whitespace".
    kind, _ = match_value("지티소프트", "공급자 (주)지티소프트, 서울")
    assert kind is MatchKind.EXACT


def test_truncated_identifiers_are_rejected():
    doc = "작성일자 2026-08-16\n등록번호 000-00-00000\n"
    assert match_value("2026-08-16", doc)[0] is MatchKind.EXACT
    assert match_value("000-00-00000", doc)[0] is MatchKind.EXACT
    assert match_value("2026-08-1", doc)[0] is MatchKind.NONE
    assert match_value("000-00-0000", doc)[0] is MatchKind.NONE


def test_a_fragment_supplier_name_is_discarded_by_the_gate():
    doc = "상호 주식회사 지티소프트\n공급가액 100원\n"
    fields = run_gate(
        [ExtractedValue("supplier_name", "주식회사 지", None, None)],
        doc,
        load_rule_pack(RULES),
    )
    assert fields[0].verdict is Verdict.DISCARDED
    assert fields[0].confidence == 0.0


# --- 9) how a figure is spelled must not decide whether arithmetic runs --------

# Space- and dot-separated thousands, as an OCR pass prints them. Every figure
# below is genuinely on the page, so grounding has nothing to object to — the
# only thing standing between a misassignment and a `verified` verdict is
# whether the gate still demands an arithmetic rule.
DOC_SPACED = (
    "전자세금계산서\n"
    "공급가액  1 000 000원\n"
    "세액        100 000원\n"
    "합계금액  1 100 000원\n"
)

DOC_DOTTED = (
    "전자세금계산서\n"
    "공급가액  1.000.000원\n"
    "세액        100.000원\n"
    "합계금액  1.100.000원\n"
)


@pytest.mark.parametrize(
    ("doc", "supply", "vat", "total"),
    [
        (DOC_SPACED, "1 000 000원", "1 000 000원", "1 100 000원"),
        (DOC_DOTTED, "1.000.000원", "1.000.000원", "1.100.000원"),
    ],
    ids=["space-separated", "dot-separated"],
)
def test_a_misassignment_cannot_hide_behind_a_thousands_separator(doc, supply, vat, total):
    # The supply figure put in the VAT slot. Spelled "1,000,000원" this is caught
    # (10% of supply is not supply), and it must be caught here too. It was not:
    # `normalize_number_str` returns None for a multi-token string, the gate read
    # that None as "purely textual", and the arithmetic requirement fell away —
    # three fields came back verified at confidence 1.0 having been checked by
    # nothing at all.
    fields = _fields(
        [
            ExtractedValue("supply", supply, None, f"공급가액  {supply}"),
            ExtractedValue("vat", vat, None, f"공급가액  {supply}"),
            ExtractedValue("total", total, None, f"합계금액  {total}"),
        ],
        doc=doc,
    )
    assert _all_discarded(fields)
    assert all(
        any(c.name == "rules_applied" and not c.passed for c in f.checks)
        for f in fields.values()
    )


def test_the_same_misassignment_is_caught_however_it_is_spelled():
    # The comma-spelled control for the case above. The two spellings are caught
    # by different halves of the gate and the verdicts differ in *scope*, which
    # is the honest thing to pin: here the numbers are readable, the 10% rule is
    # violated, and localization narrows the fault to the field that caused it;
    # in the multi-token spelling nothing can be read, so every figure fails
    # closed. What must not differ is the bad value's verdict.
    fields = _fields(
        [
            ExtractedValue("supply", "1,000,000원", None, "공급가액  1,000,000원"),
            ExtractedValue("vat", "1,000,000원", None, "공급가액  1,000,000원"),
            ExtractedValue("total", "1,100,000원", None, "합계금액  1,100,000원"),
        ]
    )
    assert fields["vat:1,000,000원"].verdict is Verdict.DISCARDED
    assert fields["vat:1,000,000원"].confidence == 0.0


def test_dates_and_identifiers_no_rule_covers_are_discarded_not_verified():
    # The module docstring promises a numeric-looking value no invariant covers
    # comes back DISCARDED "because the gate never claims more than it actually
    # checked". Multi-token identifiers used to be the exception that broke it.
    doc = "전자세금계산서\n승인번호: 20260701-41000000-12345678\n작성일자: 2026-07-01\n"
    fields = _fields(
        [
            ExtractedValue("approval_no", "20260701-41000000-12345678", None, None),
            ExtractedValue("issue_date", "2026-07-01", None, None),
        ],
        doc=doc,
    )
    assert _all_discarded(fields)


def test_a_faithful_extraction_still_verifies():
    # The counterweight: nothing above may be bought by discarding good values.
    fields = _fields(
        [
            ExtractedValue("supply", "1,000,000원", None, "공급가액  1,000,000원"),
            ExtractedValue("vat", "100,000원", None, "세액        100,000원"),
            ExtractedValue("total", "1,100,000원", None, "합계금액  1,100,000원"),
        ]
    )
    assert all(f.verdict is Verdict.VERIFIED for f in fields.values())


@pytest.mark.parametrize(
    "raw",
    ["일백만원", "삼천오백만원", "壹百萬", "百萬", "Ⅻ", "¹⁰⁰", "₁₀₀", "①", "½"],
    ids=["hangul", "hangul-long", "hanja", "hanja-short", "roman",
         "superscript", "subscript", "circled", "fraction"],
)
def test_a_quantity_written_without_digits_cannot_slip_into_a_field_a_rule_names(raw):
    # `carries_number` asks whether the value holds a number *token*, and a token
    # is ASCII digits after NFKC. That is not the only way to write a quantity:
    # 한글/한자 수사 carry no digit at all, and superscripts, fractions and Roman
    # numerals are category No/Nl — stripped as notation before the matcher sees
    # them (rightly: ①②③ number line items, they are not amounts).
    #
    # Read as "text", such a value inherited a 상호's treatment — grounded
    # verbatim, nothing else asked — so the supply amount spelled 일백만원 and
    # dropped into the VAT slot came back verified at confidence 1.0. What the
    # gate promises about a field its rule pack *names* must not depend on the
    # notation the document used, so a named field with no readable number fails
    # closed regardless of spelling.
    doc = f"전자세금계산서\n공급가액 {raw}\n세액 100,000원\n합계금액 1,100,000원\n"
    fields = run_gate(
        [ExtractedValue("vat", raw, None, f"공급가액 {raw}")],
        doc,
        load_rule_pack(RULES),
    )
    assert fields[0].verdict is Verdict.DISCARDED
    assert fields[0].confidence == 0.0
    assert any(c.name == "rules_applied" and not c.passed for c in fields[0].checks)


def test_the_same_value_is_still_text_where_no_rule_claims_it():
    # The counterweight, and the line the fix has to hold: "a rule names this
    # field" is the trigger, not "this string looks like it might be a number".
    # A field no invariant covers is decided by grounding alone, as 상호 and
    # 품목 always were — otherwise every unmodelled field becomes undiscardable
    # noise and the gate stops being usable for anything but amounts.
    doc = "전자세금계산서\n비고 일백만원\n공급가액 1,000,000원\n"
    fields = run_gate(
        [ExtractedValue("memo", "일백만원", None, "비고 일백만원")],
        doc,
        load_rule_pack(RULES),
    )
    assert fields[0].verdict is Verdict.VERIFIED


def test_a_textual_value_still_needs_no_arithmetic():
    # ...and a value carrying no digits at all is still decided by grounding
    # alone. Widening the predicate to "holds digits" must not widen it to
    # "holds anything".
    doc = "상호 주식회사 가온소프트\n공급가액 1,000,000원\n세액 100,000원\n합계금액 1,100,000원\n"
    fields = run_gate(
        [ExtractedValue("supplier_name", "주식회사 가온소프트", None, None)],
        doc,
        load_rule_pack(RULES),
    )
    assert fields[0].verdict is Verdict.VERIFIED
    assert not any(c.name == "rules_applied" for c in fields[0].checks)
