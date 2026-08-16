"""The verification gate: orchestrates grounding + arithmetic rules.

Pipeline (per document):

    extracted values ── grounding ─┐
                                   ├─► gate ─► VerifiedField[]
    rule pack ───────── arithmetic ┘

Gate policy (the core trust guarantee):

  * A field must be **grounded** (verbatim evidence exists) AND pass **all**
    arithmetic rules that reference it.
  * A caller-supplied ``number`` must **agree with ``raw``**. Grounding proves
    what the document says about ``raw``; the rule engine does its arithmetic on
    a number. If those two are allowed to differ, each half of the gate passes
    on a different figure and the pair proves nothing — quote the document
    exactly, put an invented amount in ``number``, and every check goes green.
    So ``number`` is never trusted as the value: it is cross-checked against
    ``raw`` and a mismatch discards the field (``raw_number_agreement``).
  * A field name must appear **at most once** per extraction. Arithmetic is
    computed over a field-name environment, so a repeated name means one
    instance silently supplies the number while *every* instance inherits the
    resulting check — including the one the rule never saw. Duplicates are
    discarded on both sides (``duplicate_field``).
  * A field that carries a **number** must additionally have been *reached* by
    at least one applicable arithmetic rule. Without that requirement the gate
    is fail-**open**: ``all([]) is True``, so a numeric value no rule touched —
    because no rule pack was loaded, because the pack belongs to another
    document type, or because a sibling field the rule needs is missing —
    would be reported VERIFIED although no arithmetic verification ever ran.
    Such a value is DISCARDED with a failed ``rules_applied`` check.
  * Purely textual values (상호·품목 등) carry no number, have no arithmetic
    invariant to satisfy, and are decided by grounding alone. "Numeric" here is
    the *same* predicate grounding uses (:func:`normalize_number_str`), so the
    two halves of the gate cannot disagree about what is a number: anything
    grounding treats as a number needs a rule, anything it treats as text does
    not.
  * If any of the above fails, the field is **auto-discarded**: verdict =
    DISCARDED, ``confidence = 0``. This is what kills a hallucinated /
    rule-breaking / unverified number before it reaches downstream.
  * Otherwise verdict = VERIFIED and the field keeps its extractor confidence.

The trade-off is deliberate: a numeric-looking value no invariant covers (an
invoice number, a date) comes back DISCARDED rather than VERIFIED, because the
gate never claims more than it actually checked. It says so out loud — the
field carries a ``rules_applied`` check naming the pack and the field — so a
caller can add a rule for it or carry it as an unverified identifier. Deciding
this from ``ExtractedValue.number`` alone would be gentler but useless as a
guarantee: that field is optional, so leaving it out would skip arithmetic
verification altogether, which is exactly the bypass this closes.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from typing import Any

from .grounding import ground_value, normalize_number_str
from .models import Check, ExtractedValue, Verdict, VerifiedField
from .rules import RulePack, evaluate_pack_with_fields

#: Check names the gate itself produces (rule packs supply all other names).
GROUNDING_CHECK = "grounding"
RULES_APPLIED_CHECK = "rules_applied"
RAW_NUMBER_CHECK = "raw_number_agreement"
DUPLICATE_FIELD_CHECK = "duplicate_field"

#: Check names produced by the gate itself rather than by a rule pack.
_GATE_CHECKS = frozenset(
    {GROUNDING_CHECK, RULES_APPLIED_CHECK, RAW_NUMBER_CHECK, DUPLICATE_FIELD_CHECK}
)


def value_number(value: ExtractedValue) -> float | None:
    """Numeric reading of a value, or ``None`` when it is purely textual.

    The number is always parsed from ``raw``, because ``raw`` is the string that
    grounding actually verified against the document. ``ExtractedValue.number``
    is caller-supplied and optional (so is ``number`` in the MCP tool schema);
    trusting it would let the two halves of the gate check different figures —
    grounding proving the document says 11,000,000 while the rule engine happily
    balances a supplied 1,100,000. It is cross-checked instead, by
    :func:`raw_number_agreement`, and a mismatch discards the field.

    The parse is the deterministic one grounding applies to decide whether a
    value is a number, keeping both halves of the gate on one definition.
    """
    key = normalize_number_str(value.raw)
    if key is None:
        return None
    try:
        return float(key)
    except ValueError:  # pragma: no cover - normalize_number_str output is float-safe
        return None


def raw_number_agreement(value: ExtractedValue) -> Check | None:
    """Cross-check a caller-supplied ``number`` against ``raw``.

    Returns ``None`` when the caller supplied no ``number`` and there is
    therefore nothing to disagree about.
    """
    if value.number is None:
        return None
    parsed = value_number(value)
    if parsed is None:
        return Check(
            name=RAW_NUMBER_CHECK,
            passed=False,
            detail=(
                f"number={value.number!r} was supplied but raw {value.raw!r} "
                "carries no number, so nothing grounds it"
            ),
        )
    if not math.isclose(parsed, float(value.number), rel_tol=1e-9, abs_tol=1e-6):
        return Check(
            name=RAW_NUMBER_CHECK,
            passed=False,
            detail=(
                f"number={value.number!r} disagrees with raw {value.raw!r} "
                f"(which reads as {parsed!r})"
            ),
        )
    return Check(
        name=RAW_NUMBER_CHECK,
        passed=True,
        detail=f"supplied number agrees with raw ({parsed!r})",
    )


def _duplicate_check(field: str, count: int) -> Check:
    """Fail-closed check for a field name that appears more than once."""
    return Check(
        name=DUPLICATE_FIELD_CHECK,
        passed=False,
        detail=(
            f"field {field!r} appears {count} times in one extraction; an "
            "arithmetic result cannot be attributed to a single instance"
        ),
    )


def _build_env(values: Iterable[ExtractedValue], counts: Counter[str]) -> dict[str, float]:
    """Field-name -> number map for the rule engine (numeric fields only).

    Ungrounded values are kept: a hallucinated figure that *breaks* an invariant
    should be reported as the violation it is ("999,999 vs 100,000"), which is
    far more useful than "no rule applied". What such a value must never do is
    make a rule *pass* — see :func:`_vouching_check` for that half.

    A field name that appears more than once is withheld. Mapping it would
    silently keep whichever instance came last, and every same-named instance
    would then inherit a check computed from a number it does not carry.
    Withholding it makes the rules referencing it unevaluable, which the
    fail-closed guard in :func:`run_gate` turns into a discard.
    """
    env: dict[str, float] = {}
    for v in values:
        if counts[v.field] > 1:
            continue
        number = value_number(v)
        if number is not None:
            env[v.field] = number
    return env


def _vouching_check(check: Check, referenced: set[str], grounded: set[str]) -> Check:
    """Downgrade a rule that only passes by leaning on an ungrounded value.

    An invariant holding across the extractor's own numbers says nothing about
    the document unless those numbers came *from* the document. Otherwise a
    self-consistent invention verifies itself: claim a supply and a vat that no
    line contains, pick a total that happens to appear elsewhere on the page,
    and ``total == supply + vat`` balances perfectly — vouching for the one
    figure that did ground. Only a passing check is downgraded; a failing one is
    already refusing to vouch, and its detail is the useful diagnostic.
    """
    if not check.passed or referenced <= grounded:
        return check
    missing = ", ".join(sorted(referenced - grounded))
    return Check(
        name=check.name,
        passed=False,
        detail=(
            f"{check.detail}; but this verifies nothing — the rule leans on "
            f"{missing}, which is not grounded in the document"
        ),
    )


def _unverified_check(value: ExtractedValue, rule_pack: RulePack | None) -> Check:
    """Fail-closed check for a number that no arithmetic rule reached."""
    if rule_pack is None:
        reason = "no rule pack was loaded for this document"
    else:
        reason = (
            f"no rule in pack {rule_pack.doc_type!r} applies to {value.field!r} "
            "(field not referenced by any rule, or a field a rule needs is missing)"
        )
    return Check(
        name=RULES_APPLIED_CHECK,
        passed=False,
        detail=f"numeric value was not arithmetically verified: {reason}",
    )


def run_gate(
    values: list[ExtractedValue],
    full_text: str,
    rule_pack: RulePack | None = None,
) -> list[VerifiedField]:
    """Verify every extracted value and return per-field verdicts."""
    # Grounding runs first: which values are grounded decides which rule results
    # are allowed to vouch for a field (see :func:`_vouching_check`).
    groundings = [ground_value(v.raw, v.grounding_quote, full_text) for v in values]
    counts = Counter(v.field for v in values)
    grounded = {v.field for v, g in zip(values, groundings, strict=True) if g.passed}
    env = _build_env(values, counts)

    # Arithmetic checks are computed once over the whole field set, then
    # attributed to each field the rule references.
    rule_checks: list[tuple[Check, set[str]]] = (
        evaluate_pack_with_fields(rule_pack, env) if rule_pack is not None else []
    )

    results: list[VerifiedField] = []
    for v, grounding in zip(values, groundings, strict=True):
        # Gate-level integrity checks on the value itself, before any arithmetic.
        integrity: list[Check] = []
        agreement = raw_number_agreement(v)
        if agreement is not None:
            integrity.append(agreement)
        if counts[v.field] > 1:
            integrity.append(_duplicate_check(v.field, counts[v.field]))

        # every arithmetic rule that references this field
        arithmetic = [
            _vouching_check(check, fields, grounded)
            for check, fields in rule_checks
            if v.field in fields
        ]
        if not arithmetic and value_number(v) is not None:
            # FAIL-CLOSED: a number nothing checked is unverified, not "fine".
            # This is also what keeps the all() below from passing vacuously.
            arithmetic.append(_unverified_check(v, rule_pack))

        passed = grounding.passed and all(c.passed for c in (*integrity, *arithmetic))

        results.append(
            VerifiedField(
                field=v.field,
                value=v,
                checks=[grounding, *integrity, *arithmetic],
                verdict=Verdict.VERIFIED if passed else Verdict.DISCARDED,
                # AUTO-DISCARD: any failure zeroes confidence, blocking downstream.
                confidence=v.confidence if passed else 0.0,
            )
        )
    return results


def summarize(
    fields: list[VerifiedField],
    rule_pack: RulePack | str | None = None,
) -> dict[str, Any]:
    """Small report: counts for the CLI / bench / viewer / MCP.

    ``rule_pack`` (name or ``None``) and ``rules_applied`` (how many distinct
    arithmetic rules actually ran over these fields) let a caller tell "the
    gate verified this" apart from "the gate had nothing to verify with":
    ``rules_applied == 0`` means no arithmetic ran at all. Pass the pack that
    was handed to :func:`run_gate` to have it named in the report.
    """
    verified = sum(1 for f in fields if f.verdict is Verdict.VERIFIED)
    discarded = len(fields) - verified
    ungrounded = sum(1 for f in fields if not f.grounded)
    applied = {c.name for f in fields for c in f.checks if c.name not in _GATE_CHECKS}
    pack_name = rule_pack.doc_type if isinstance(rule_pack, RulePack) else rule_pack
    return {
        "total": len(fields),
        "verified": verified,
        "discarded": discarded,
        "ungrounded": ungrounded,
        "rule_pack": pack_name,
        "rules_applied": len(applied),
    }
