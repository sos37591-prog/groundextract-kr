"""Deterministic verbatim grounding.

Given a value the model claims to have extracted and the source text (the
document, or a specific span), decide whether that value *actually exists* in
the source. This is the first half of the trust gate: a number the model
invented (numeric hallucination) has no verbatim support and must fail here.

Three-tier, deterministic judgement (no randomness, no network):

  1. EXACT           - the value string occurs verbatim in the source.
  2. PARTIAL_NUMERIC - the *number* occurs, only the formatting differs
                       (thousands separators, currency symbols, decimals,
                       full-width digits, Korean units like 원/₩, and the
                       KR negative notations △/▲ and accounting parentheses).
  3. FUZZY           - normalized strings are within an edit-distance ratio
                       threshold (handles OCR noise / stray whitespace).
                       **Non-numeric values only** - see below.

Anything below the fuzzy threshold => MatchKind.NONE (ungrounded).

Which tier is even reachable depends on what the value is, and the split matters
more than the tiers themselves:

  * A value holding **one number** is decided by comparing canonical number
    *tokens*, never by substring. "1,000,000원" occurs inside "11,000,000원", so
    a substring test would accept a 10x digit shift - the most common OCR/LLM
    corruption - as verbatim evidence for a document that never said it. EXACT
    is then only a label applied on top of a token match that already succeeded.
  * A value holding **digits but no single number** ("1 234 567원", "2026-08-16")
    has no canonical figure to compare and must occur verbatim or not at all.
  * A value holding **no digits** (상호·품목 등) is the only one offered the
    fuzzy tier.

Why FUZZY never applies to a numeric value
------------------------------------------
An edit-distance ratio cannot tell "a smudged glyph" apart from "a different
amount": ``1,900,000원`` against ``공급가액  1,000,000원`` scores 0.90, well over
the threshold. The effect is worst exactly where it matters most - for 7- and
8-digit KRW figures (백만~수천만원, the dominant magnitude in tax invoices and
financial statements) *every* single-digit corruption cleared the threshold, so
a wrong number would have been reported as grounded.

So a value that parses as a number must match EXACT or PARTIAL_NUMERIC, never
FUZZY. Fuzzy tolerance is kept for genuinely textual fields (상호·품목 등), where
OCR noise is real and a one-character slip is not a different fact.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from .models import Check, MatchKind

# Ratio at/above which a normalized fuzzy match is accepted (text values only).
FUZZY_THRESHOLD = 0.87

# Largest source the fuzzy tier will walk. Fuzzy matching slides a window across
# the whole source for *every* value compared, so its cost is
# O(len(source)) per value with a SequenceMatcher call at each step — by far the
# most expensive path here, and the only one that was unbounded. A caller handing
# a multi-megabyte "document" plus a few dozen textual values could buy minutes
# of CPU with one well-formed request, and a server processing requests in
# sequence (as the MCP stdio loop does) stops answering anyone else meanwhile.
# Past this size the tier declines instead of grinding: refusing to ground is the
# fail-closed direction, so the worst outcome is a discarded field — never a
# false verification. Real regulatory documents are far below the limit.
MAX_FUZZY_SOURCE_CHARS = 64 * 1024

# ...and a hard budget on the work itself, because a size limit does not bound it.
#
# Two earlier attempts failed, and how they failed is why this one is shaped the
# way it is.
#
# The first predicted the cost as source_tokens x value_tokens and declined when
# the product was too large. Re-measured, budget consumption and wall clock do
# move together, but far too weakly — across the shapes swept, a 114x spread in
# budget bought a 6.7x spread in time — so the limit sat far looser than its
# slowest observed input, and shapes costing tens of seconds passed while
# spending a few percent of their allowance.
#
# The lesson is that SequenceMatcher's cost is data-dependent (autojunk alone
# swings it by an order of magnitude) and not worth predicting. So this budget is
# not a prediction: :func:`_best_fuzzy_ratio` decrements it by the actual
# comparison size at every step and stops when it runs out. That cannot be gamed
# by input shape, because it measures the work instead of guessing it, and the
# walk order is fixed so the cut-off point is deterministic — the same request
# always gets the same answer.
#
# The second attempt got that right and still mispriced, by charging
# len(value) x len(span) alone. Every comparison also pays a cost that depends on
# neither string — constructing the SequenceMatcher, slicing and joining the
# span, one iteration of the loop — so a *short* value was billed 2 units per
# call and fitted tens of thousands of calls inside the budget. Short values, not
# long ones, turned out to be the expensive family. Hence
# FUZZY_CALL_OVERHEAD_CHARS below, which prices that floor.
#
# Stopping early returns whatever the walk had found, so a match already located
# still counts and a value that had not matched yet comes back ungrounded. That
# is the fail-closed direction: the cost is a discarded field the caller can
# rescue by citing a grounding_quote, never a false verification.
MAX_FUZZY_COMPARISON_CHARS = 1_000_000

#: What one comparison costs before either string is looked at. Charging only
#: len(value) x len(span) prices a 2-character value at 2 units per call, which
#: is how short values became the worst family rather than the cheapest; this
#: floor dominates exactly when both strings are small, which is the case where
#: it really is the whole cost.
FUZZY_CALL_OVERHEAD_CHARS = 96

#: ...and a budget for the whole request, because the one above is per *value*
#: and a caller may send MAX_VALUES of them. Bounding each value in isolation
#: bounds nothing about the request: 256 values, each legitimately inside its own
#: allowance, still add up to minutes of CPU on a loop that answers one caller at
#: a time. :func:`groundextract.gate.run_gate` opens one of these and every value
#: spends from it, so what a request can ask for is bounded once rather than per
#: field. Values arriving after it is exhausted skip the fuzzy tier and come back
#: ungrounded — the same fail-closed direction as the per-value cut-off.
MAX_FUZZY_REQUEST_CHARS = 4_000_000

# Characters that are noise around numbers in KR financial docs.
_CURRENCY_UNITS = ("원", "₩", "KRW", "krw")

# Korean financial statements write a negative with a triangle prefix (결손금,
# 감소액: "△1,234" / "▲1,234"); accounting convention also wraps it in
# parentheses ("(1,234)"). Both mean minus.
_NEG_TRIANGLES = ("△", "▲")

# Digits with optional thousands separators and decimals.
_NUM_BODY = r"\d[\d,]*(?:\.\d+)?"

# One numeric token *including* its sign decoration. Order matters: the
# accounting-parenthesis form is tried first so "(1,234)" is read as -1234
# rather than +1234.
#
# The parenthesis branch requires the parens to wrap the number *alone*, which
# is what keeps ordinary Korean parentheticals out: "(주)일송무역" and
# "(단위: 원)" contain no bare number and are never read as negatives. The
# triangle/minus prefix must likewise sit directly against the first digit, so
# a bulleted line such as "△ 매출액 1,234" stays positive.
_NUMBER_TOKEN = re.compile(
    rf"\(\s*{_NUM_BODY}\s*\)"
    rf"|[{''.join(_NEG_TRIANGLES)}-]?{_NUM_BODY}"
)


def _nfkc(text: str) -> str:
    """Normalize full-width digits/letters to ASCII and unify compat forms."""
    return unicodedata.normalize("NFKC", text)


def _drop_enclosed_numerals(text: str) -> str:
    """Remove numerals that are notation rather than quantity (①, ⑴, ², ½).

    NFKC rewrites these to bare digits, which would hand the matcher number
    tokens that are not amounts at all — Korean forms number their line items
    ①②③, so a document would offer a "1", a "2" and a "3" as grounding for
    figures it never stated. Unicode already separates the two cases: enclosed,
    superscript and fraction forms are category ``No`` (other number), while
    full-width digits (１２３) — which are genuinely digits, just typeset wide —
    are ``Nd`` and must survive.
    """
    return "".join(ch for ch in text if unicodedata.category(ch) != "No")


#: Normalizing a document is O(len(text)), and the gate compares *every*
#: extracted value against the *same* ``full_text`` — so the naive path pays for
#: the whole document once per field, which is what made a large document
#: expensive enough to matter (see :data:`MAX_FUZZY_SOURCE_CHARS`). One request
#: touches one or two distinct sources, so a small cache collapses that to a
#: single pass. Python memoizes ``str`` hashes on the object, so repeat lookups
#: with the same document are O(1) rather than O(len(text)).
_NORMALIZE_CACHE_SIZE = 8

#: Only inputs at least this long are cached. Values are short and every field
#: brings a different one, so caching them would evict the one entry that
#: actually pays for itself — the document — on every single comparison, which
#: is the same cost as having no cache at all.
_CACHE_MIN_CHARS = 4096


def _numeric_text_impl(text: str) -> str:
    return _nfkc(_drop_enclosed_numerals(text))


_numeric_text_cached = lru_cache(maxsize=_NORMALIZE_CACHE_SIZE)(_numeric_text_impl)


def _numeric_text(text: str) -> str:
    """Text prepared for number tokenization (document-sized inputs cached)."""
    if len(text) < _CACHE_MIN_CHARS:
        return _numeric_text_impl(text)
    return _numeric_text_cached(text)


def _canonical_number(token: str) -> str:
    """Canonicalize one ``_NUMBER_TOKEN`` match into a comparable key string."""
    tok = token.strip()
    negative = False
    if tok.startswith("(") and tok.endswith(")"):
        negative, tok = True, tok[1:-1].strip()
    elif tok.startswith(_NEG_TRIANGLES) or tok.startswith("-"):
        negative, tok = True, tok[1:]

    num = tok.replace(",", "")
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    # Never emit "-0": (0) and 0 are the same number.
    if negative and any(ch in "123456789" for ch in num):
        num = f"-{num}"
    return num


def normalize_number_str(s: str) -> str | None:
    """Extract a canonical numeric key from a string, or None if there is no
    *single* number in it.

    "1,000,000원" -> "1000000"   ;   "₩ 1,000,000.00" -> "1000000"
    "10%" -> "10"                ;   "-500" -> "-500"
    "△1,234" -> "-1234"          ;   "(1,234)" -> "-1234"
    Trailing ``.0`` zeros are trimmed so 100 == 100.00.

    A string holding **more than one** number token has no single canonical
    reading and returns ``None``. Taking the first token instead was a quiet
    source of false evidence: space-separated thousands ("1 234 567원", common
    in OCR output and European typesetting) reduced to the key ``"1"``, which
    then matched any stray "1" in the document. Callers must treat ``None`` as
    "not a single number", never as "no digits" — see :func:`match_value`, which
    still refuses fuzzy matching for such a string.
    """
    s = _numeric_text(s)
    for unit in _CURRENCY_UNITS:
        s = s.replace(unit, "")
    tokens = _NUMBER_TOKEN.findall(s)
    if len(tokens) != 1:
        return None
    return _canonical_number(tokens[0])


def carries_number(s: str) -> bool:
    """Does ``s`` hold at least one number token?

    The companion to :func:`normalize_number_str`, which answers the narrower
    question "is this *one* number?" and returns ``None`` for two very different
    strings: text with no digits at all ("주식회사 가온소프트") and a string
    holding several tokens ("1 000 000원", "2026-06-27").

    Anything deciding "does this value still owe us arithmetic verification?"
    must ask *this* question instead. Reading that ``None`` as "not a number"
    hands every multi-token spelling a free pass, and space- or dot-separated
    thousands are exactly the OCR forms the gate exists to distrust.
    """
    s = _numeric_text(s)
    for unit in _CURRENCY_UNITS:
        s = s.replace(unit, "")
    return _NUMBER_TOKEN.search(s) is not None


def _number_keys_impl(text: str) -> dict[str, str]:
    """Map every number in ``text`` to the first token that spelled it.

    Built once per source instead of re-scanning the whole document for each
    value compared against it — the difference between O(fields x document) and
    O(document) on a request that verifies dozens of fields.
    """
    keys: dict[str, str] = {}
    for m in _NUMBER_TOKEN.finditer(_numeric_text(text)):
        tok = m.group(0)
        keys.setdefault(_canonical_number(tok), tok)
    return keys


_number_keys_cached = lru_cache(maxsize=_NORMALIZE_CACHE_SIZE)(_number_keys_impl)


def _number_keys(text: str) -> dict[str, str]:
    """Number index for a source (document-sized inputs cached). Read-only."""
    if len(text) < _CACHE_MIN_CHARS:
        return _number_keys_impl(text)
    return _number_keys_cached(text)


def _normalize_text_impl(text: str) -> str:
    return re.sub(r"\s+", " ", _nfkc(text)).strip()


_normalize_text_cached = lru_cache(maxsize=_NORMALIZE_CACHE_SIZE)(_normalize_text_impl)


def _normalize_text(text: str) -> str:
    """Whitespace/compat-normalize for comparison (document-sized inputs cached)."""
    if len(text) < _CACHE_MIN_CHARS:
        return _normalize_text_impl(text)
    return _normalize_text_cached(text)


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class FuzzyBudget:
    """Fuzzy-matching work one request may spend, shared across its values.

    :data:`MAX_FUZZY_COMPARISON_CHARS` bounds a single value's span walk, which
    bounds nothing about a request carrying ``MAX_VALUES`` of them — each one
    comfortably inside its own allowance still summed to minutes of CPU on a loop
    that answers one caller at a time. :func:`groundextract.gate.run_gate` opens
    one budget and hands it to every value, so the request is bounded once.

    Callers that ground a value on its own get a fresh budget and are unaffected.
    """

    __slots__ = ("remaining",)

    def __init__(self, total: int = MAX_FUZZY_REQUEST_CHARS) -> None:
        self.remaining = total

    def spend(self, cost: int) -> bool:
        """Charge ``cost``; ``False`` once the request has run out."""
        self.remaining -= cost
        return self.remaining >= 0


def _best_fuzzy_ratio(v_norm: str, s_norm: str, budget: FuzzyBudget | None = None) -> float:
    """Best ratio of the value against token-aligned spans of the source.

    The candidates a fuzzy comparison is scored against decide what the tier
    actually tolerates. Scoring against raw character slices whose width came
    from the *value* length made it incoherent: a truncated value got a
    correspondingly short window, which then matched its own surviving prefix
    well. Against 상호 "주식회사 지티소프트", the six-character truncation
    "주식회사 지티" scored 0.88 and passed, while the one-character slip
    "주식회사 지티소프" — the far likelier OCR failure, and the one this tier
    exists for — scored 0.80 and failed.

    Spans that begin and end on token boundaries remove that artefact: every
    candidate is a phrase the document actually contains, so the score reflects
    how close the value is to something written there rather than to a slice
    chosen to flatter it. The width is allowed to differ by one token, which
    covers an extractor that swallowed or split a space.
    """
    tokens = s_norm.split(" ")
    wanted = max(len(v_norm.split(" ")), 1)
    best = 0.0
    # Spent as the walk proceeds rather than estimated in advance — see
    # MAX_FUZZY_COMPARISON_CHARS for why predicting this cost does not work.
    # `mine` bounds this value; `budget` bounds the request it belongs to.
    mine = MAX_FUZZY_COMPARISON_CHARS
    for width in sorted({max(wanted - 1, 1), wanted, wanted + 1}):
        for i in range(0, max(len(tokens) - width + 1, 1)):
            span = " ".join(tokens[i : i + width])
            # len(value) x len(span) is SequenceMatcher's worst case, plus the
            # per-call floor that depends on neither string. Without the floor a
            # two-character value costs two units and tens of thousands of calls
            # fit inside the budget, which made short values the expensive family.
            cost = len(v_norm) * len(span) + FUZZY_CALL_OVERHEAD_CHARS
            mine -= cost
            if mine < 0:
                return best
            if budget is not None and not budget.spend(cost):
                return best
            best = max(best, _fuzzy_ratio(v_norm, span))
            if best >= FUZZY_THRESHOLD:
                return best
    return best


def _occurs_standalone(needle: str, haystack: str) -> bool:
    """True when ``needle`` occurs in ``haystack`` as a whole token, not a fragment.

    A plain substring test accepts any slice of the document, which for a textual
    field is the whole verification: 상호·품목 carry no arithmetic invariant, so
    grounding is the only check they get. Against "상호 주식회사 지티소프트", the
    substring test grounds "주식회사 지티소프" — and "주" — at confidence 1.0,
    although a truncated company name is a *different company*, and dropping
    trailing characters is exactly what OCR does.

    The boundary is "not adjacent to another alphanumeric character" rather than
    a whitespace rule: Korean does not space its syllables, but it does abut
    punctuation, so 상호 followed by "," or ")" must still ground.
    """
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before_ok = start == 0 or not haystack[start - 1].isalnum()
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


def match_value(
    value: str, source: str, budget: FuzzyBudget | None = None
) -> tuple[MatchKind, str]:
    """Return (kind, detail) describing how ``value`` grounds in ``source``.

    ``value``  : the string the model claims (e.g. "1,000,000").
    ``source`` : the text span/page the model cites, or the whole document.
    """
    if not value or not source:
        return MatchKind.NONE, "empty value or source"

    v_norm = _normalize_text(value)
    s_norm = _normalize_text(source)

    # 1) NUMERIC: decided by whole-number-token comparison, never by substring.
    #
    #    A raw substring test is unsound for numbers because a shorter figure is
    #    a substring of a longer one: "1,000,000원" occurs inside "11,000,000원",
    #    so a 10x-shifted amount - the dominant OCR/LLM digit error - would be
    #    reported as verbatim evidence for a document that never contained it.
    #    Comparing canonical *tokens* gives the digit boundary that substring
    #    matching lacks. EXACT is still reported when the value string itself is
    #    also present verbatim; that is a labelling detail on top of a match the
    #    token comparison already established.
    v_num = normalize_number_str(value)
    if v_num is not None:
        tok = _number_keys(source).get(v_num)
        if tok is not None:
            if v_norm and v_norm in s_norm:
                return MatchKind.EXACT, f"verbatim substring: {v_norm!r}"
            return (
                MatchKind.PARTIAL_NUMERIC,
                f"number {v_num} present with different formatting ({tok!r})",
            )
        # Numeric values stop here: no fuzzy tier. An edit-distance ratio cannot
        # separate OCR noise from a genuinely different amount (1,900,000 vs
        # 1,000,000 scores 0.90), and 7-8 digit KRW figures - the bulk of tax
        # invoices and financial statements - passed every single-digit
        # corruption that way. See the module docstring.
        return (
            MatchKind.NONE,
            f"number {v_num} not found in source "
            "(fuzzy matching is not applied to numeric values)",
        )

    # 1b) DIGIT-BEARING but not a single number ("1 234 567원", "2026-08-16",
    #     "400,000 + 600,000"). There is no canonical figure to compare, and the
    #     fuzzy tier must not see it either — an edit-distance ratio separates
    #     "2026-08-16" from "2026-08-17" no better than it separates amounts. It
    #     has to appear verbatim, as a whole token, or not at all.
    if any(ch.isdigit() for ch in _nfkc(value)):
        if v_norm and _occurs_standalone(v_norm, s_norm):
            return MatchKind.EXACT, f"verbatim: {v_norm!r}"
        return (
            MatchKind.NONE,
            "value carries digits but no single number; it must appear verbatim "
            "(fuzzy matching is not applied to digit-bearing values)",
        )

    # 2) EXACT verbatim occurrence - textual values only (see above for why a
    #    numeric value never reaches this branch). Whole-token, not substring:
    #    a truncated 상호 is a different company, not weaker evidence for the
    #    same one, and grounding is the *only* check a textual field gets.
    if v_norm and _occurs_standalone(v_norm, s_norm):
        return MatchKind.EXACT, f"verbatim: {v_norm!r}"

    # 3) FUZZY: non-numeric values only (상호·품목 등), scored against
    #    token-aligned spans of the source (OCR noise tolerance).
    if len(s_norm) > MAX_FUZZY_SOURCE_CHARS:
        return (
            MatchKind.NONE,
            f"source too large for fuzzy matching "
            f"({len(s_norm)} chars > {MAX_FUZZY_SOURCE_CHARS}); "
            "cite a grounding_quote to ground a textual value in a document this size",
        )
    best = _best_fuzzy_ratio(v_norm, s_norm, budget)
    if best >= FUZZY_THRESHOLD:
        return MatchKind.FUZZY, f"fuzzy match ratio={best:.2f} (>= {FUZZY_THRESHOLD})"

    return MatchKind.NONE, f"no grounding (best fuzzy ratio={best:.2f})"


def ground_value(
    value: str,
    grounding_quote: str | None,
    full_text: str,
    budget: FuzzyBudget | None = None,
) -> Check:
    """Produce a grounding ``Check`` for one extracted value.

    When the model cites a ``grounding_quote``, **three** conditions must hold:

      1. the quote occurs verbatim in ``full_text``,
      2. the value occurs in the quote, and
      3. the value *also* stands on its own against ``full_text``.

    Without (1) and (3) a cited span can launder an invented figure. A quote is
    itself a string carrying numbers, so judging its existence with the same
    tiered matcher used for values accepted it as long as *one* of its numbers
    appeared in the document: stapling a real figure onto an invented one
    ("1,000,000 공급가액 2,000,000원") made the invented half count as verbatim
    evidence. Condition (1) is therefore a plain verbatim test, and (3) re-checks
    the value against the document with the digit-boundary-aware matcher so a
    quote that is itself only a substring of a longer figure cannot stand in.

    A passing check requires MatchKind in {EXACT, PARTIAL_NUMERIC, FUZZY} — and
    FUZZY is unreachable for anything carrying a number.
    """
    if grounding_quote:
        # (1) the cited span must be real, verbatim.
        if _normalize_text(grounding_quote) not in _normalize_text(full_text):
            return Check(
                name="grounding",
                passed=False,
                detail=f"cited quote not found verbatim in document: {grounding_quote!r}",
                kind=MatchKind.NONE,
            )
        # (2) the value must live inside the span the model pointed at.
        kind, detail = match_value(value, grounding_quote, budget)
        # (3) and it must survive on its own against the whole document.
        if kind is not MatchKind.NONE:
            doc_kind, doc_detail = match_value(value, full_text, budget)
            if doc_kind is MatchKind.NONE:
                return Check(
                    name="grounding",
                    passed=False,
                    detail=f"value not grounded in document: {doc_detail}",
                    kind=MatchKind.NONE,
                )
    else:
        kind, detail = match_value(value, full_text, budget)

    passed = kind is not MatchKind.NONE
    return Check(name="grounding", passed=passed, detail=detail, kind=kind)
