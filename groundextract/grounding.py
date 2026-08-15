"""Deterministic verbatim grounding.

Given a value the model claims to have extracted and the source text (the
document, or a specific span), decide whether that value *actually exists* in
the source. This is the first half of the trust gate: a number the model
invented (numeric hallucination) has no verbatim support and must fail here.

Three-tier, deterministic judgement (no randomness, no network):

  1. EXACT           - the value string is a verbatim substring of the source.
  2. PARTIAL_NUMERIC - the *number* appears, only the formatting differs
                       (thousands separators, currency symbols, decimals,
                       full-width digits, Korean units like 원/₩, and the
                       KR negative notations △/▲ and accounting parentheses).
  3. FUZZY           - normalized strings are within an edit-distance ratio
                       threshold (handles OCR noise / stray whitespace).
                       **Non-numeric values only** - see below.

Anything below the fuzzy threshold => MatchKind.NONE (ungrounded).

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

from .models import Check, MatchKind

# Ratio at/above which a normalized fuzzy match is accepted (text values only).
FUZZY_THRESHOLD = 0.87

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
    """Extract a canonical numeric key from a string, or None if no number.

    "1,000,000원" -> "1000000"   ;   "₩ 1,000,000.00" -> "1000000"
    "10%" -> "10"                ;   "-500" -> "-500"
    "△1,234" -> "-1234"          ;   "(1,234)" -> "-1234"
    Trailing ``.0`` zeros are trimmed so 100 == 100.00.
    """
    s = _nfkc(s)
    for unit in _CURRENCY_UNITS:
        s = s.replace(unit, "")
    m = _NUMBER_TOKEN.search(s)
    if not m:
        return None
    return _canonical_number(m.group(0))


def _normalize_text(text: str) -> str:
    """Whitespace/compat-normalize for fuzzy comparison."""
    return re.sub(r"\s+", " ", _nfkc(text)).strip()


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_value(value: str, source: str) -> tuple[MatchKind, str]:
    """Return (kind, detail) describing how ``value`` grounds in ``source``.

    ``value``  : the string the model claims (e.g. "1,000,000").
    ``source`` : the text span/page the model cites, or the whole document.
    """
    if not value or not source:
        return MatchKind.NONE, "empty value or source"

    v_norm = _normalize_text(value)
    s_norm = _normalize_text(source)

    # 1) EXACT verbatim substring (on NFKC/whitespace-normalized text).
    if v_norm and v_norm in s_norm:
        return MatchKind.EXACT, f"verbatim substring: {v_norm!r}"

    # 2) PARTIAL NUMERIC: the number is present, formatting differs.
    v_num = normalize_number_str(value)
    if v_num is not None:
        # collect every number token in the source and compare canonically
        for m in _NUMBER_TOKEN.finditer(_nfkc(source)):
            tok = m.group(0)
            if _canonical_number(tok) == v_num:
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

    # 3) FUZZY: non-numeric values only (상호·품목 등). Sliding best window of
    #    source vs value (OCR noise tolerance).
    #    Compare against source substrings of similar length for a fair ratio.
    best = 0.0
    win = max(len(v_norm), 1)
    # step through candidate windows; cap work for very long sources
    step = max(win // 2, 1)
    for i in range(0, max(len(s_norm) - win + 1, 1), step):
        window = s_norm[i : i + win + 2]
        best = max(best, _fuzzy_ratio(v_norm, window))
        if best >= FUZZY_THRESHOLD:
            break
    if best >= FUZZY_THRESHOLD:
        return MatchKind.FUZZY, f"fuzzy match ratio={best:.2f} (>= {FUZZY_THRESHOLD})"

    return MatchKind.NONE, f"no grounding (best fuzzy ratio={best:.2f})"


def ground_value(value: str, grounding_quote: str | None, full_text: str) -> Check:
    """Produce a grounding ``Check`` for one extracted value.

    Prefers the model-cited ``grounding_quote`` (must itself exist in the
    document AND contain the value); falls back to the whole ``full_text``.
    A passing check requires MatchKind in {EXACT, PARTIAL_NUMERIC, FUZZY} —
    and FUZZY is unreachable for anything carrying a number, so a cited quote
    whose figure is absent from the document is rejected outright.
    """
    # If the model cited a quote, that quote must be real (exist in the doc)
    # AND the value must live inside that quote. This blocks "cite a fake span".
    if grounding_quote:
        quote_kind, _ = match_value(grounding_quote, full_text)
        if quote_kind is MatchKind.NONE:
            return Check(
                name="grounding",
                passed=False,
                detail=f"cited quote not found in document: {grounding_quote!r}",
                kind=MatchKind.NONE,
            )
        kind, detail = match_value(value, grounding_quote)
    else:
        kind, detail = match_value(value, full_text)

    passed = kind is not MatchKind.NONE
    return Check(name="grounding", passed=passed, detail=detail, kind=kind)
