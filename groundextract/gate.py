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
  * Purely textual values (상호·품목 등) carry no digits, have no arithmetic
    invariant to satisfy, and are decided by grounding alone. The predicate is
    :func:`carries_number` — *any* number token — and deliberately **not**
    :func:`normalize_number_str`, which answers the narrower "is this one
    number?" and returns ``None`` for text and multi-token spellings alike.
    Reading that ``None`` as "text" was itself a bypass: "1 000 000원" and
    "1.000.000" (space- and dot-separated thousands, ordinary OCR output for
    Korean invoices) skipped arithmetic entirely and came back VERIFIED at full
    confidence, while the very same misassignment spelled "1,000,000원" was
    discarded. A value that holds digits owes the gate a rule, however it is
    spelled.
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
from itertools import combinations
from typing import Any

from .grounding import carries_number, ground_value, normalize_number_str
from .models import Check, ExtractedValue, Verdict, VerifiedField
from .rules import RulePack, evaluate_pack_with_fields, pack_fields

#: Check names the gate itself produces (rule packs supply all other names).
GROUNDING_CHECK = "grounding"
RULES_APPLIED_CHECK = "rules_applied"
RAW_NUMBER_CHECK = "raw_number_agreement"
DUPLICATE_FIELD_CHECK = "duplicate_field"
FAULT_LOCALIZATION_CHECK = "fault_localization"

#: Check names produced by the gate itself rather than by a rule pack.
_GATE_CHECKS = frozenset(
    {
        GROUNDING_CHECK,
        RULES_APPLIED_CHECK,
        RAW_NUMBER_CHECK,
        DUPLICATE_FIELD_CHECK,
        FAULT_LOCALIZATION_CHECK,
    }
)

#: Ceilings on the fault-localization search. A rule pack references a handful of
#: fields, so the real search is tiny; these only stop a pathological input from
#: turning a combinatorial search loose. Exceeding either means "cannot localize",
#: which falls back to blaming every field the failing rules reference.
MAX_LOCALIZATION_CANDIDATES = 16
MAX_LOCALIZATION_DEPTH = 3


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


def value_carries_number(value: ExtractedValue) -> bool:
    """Does ``raw`` hold digits at all — the predicate that demands a rule?

    Broader on purpose than :func:`value_number`. A value spelled "1 000 000원"
    has no single canonical reading, so :func:`value_number` returns ``None``;
    treating that as "purely textual" let the whole arithmetic requirement fall
    away and reported the value VERIFIED. Whether a number can be *read* decides
    what arithmetic runs; whether digits are *present* decides whether the field
    owed arithmetic in the first place, and those are different questions.
    """
    return carries_number(value.raw)


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


def _localize_fault(failing: list[set[str]], cleared: set[str]) -> set[str] | None:
    """Which fields could account for every failing rule, or ``None`` if unclear.

    A violated invariant names several fields but only indicts some of them. The
    gate used to discard all of them, which is why precision was the price of
    recall: a hallucinated 세액 took 공급가액 and 합계금액 down with it.

    Fields corroborated by a rule that *passed* are set aside first, then the
    smallest sets of the remainder that touch every failing rule are computed —
    the smallest explanations for what went wrong. **All** minimum-size
    explanations are blamed together, not one chosen among them, and that is what
    keeps recall intact for a single bad value:

        A single wrong field must appear in every rule that failed (nothing else
        changed), so ``{that field}`` is itself a minimum hitting set and is
        therefore always among the sets returned. Blaming their union cannot drop
        it.

    Two assumptions come with that, and both are documented as limits: several
    fields wrong at once may admit an explanation smaller than the truth, and a
    wrong value that happens to satisfy some other invariant is cleared by it.
    Returning ``None`` (no candidates, or a search wider than the ceilings above)
    means the caller keeps the old blame-everything behaviour.
    """
    candidates = sorted(set().union(*failing) - cleared) if failing else []
    if not candidates or len(candidates) > MAX_LOCALIZATION_CANDIDATES:
        return None
    for size in range(1, min(len(candidates), MAX_LOCALIZATION_DEPTH) + 1):
        explanations = [
            set(combo)
            for combo in combinations(candidates, size)
            if all(set(combo) & rule for rule in failing)
        ]
        if explanations:
            return set().union(*explanations)
    return None


def _exonerated_check(rule_name: str, blamed: Iterable[str]) -> Check:
    """Informational check recording a failure this field is not implicated in."""
    culprits = ", ".join(sorted(blamed))
    return Check(
        name=FAULT_LOCALIZATION_CHECK,
        passed=True,
        detail=(
            f"{rule_name} failed, but the inconsistency is fully explained by "
            f"{culprits} (discarded); this field is not implicated"
        ),
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
    """Fail-closed check for a value carrying digits that no arithmetic reached."""
    if value_number(value) is None:
        # Digits are there but they do not spell one number ("1 000 000원",
        # "2026-06-27"). No rule could have been evaluated over it even had the
        # pack named the field, so name that rather than blaming the pack.
        reason = (
            f"raw {value.raw!r} carries digits but no single number "
            "(space- or dot-separated thousands, a date, an identifier), "
            "so no arithmetic rule could read a value from it"
        )
    elif rule_pack is None:
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
    # A rule can end up failing for two very different reasons, and only one of
    # them is a fault to localize:
    #
    #   * the invariant is **violated** — some field's number is wrong, and the
    #     question "which one?" is worth asking;
    #   * the invariant held but was **downgraded** because it leaned on an
    #     ungrounded value (see :func:`_vouching_check`) — meaning it verified
    #     nothing at all. There is no fault to pin on anyone, and exonerating a
    #     field from it would hand it the verdict the downgrade exists to deny.
    #
    # So localization runs over violations only, and a downgraded rule keeps
    # failing for every field it references.
    # Field names the pack mentions at all — not the ones whose rules happened to
    # evaluate. See the fail-closed branch below.
    covered = pack_fields(rule_pack) if rule_pack is not None else frozenset()

    vouched: list[tuple[Check, set[str], bool]] = []
    for check, fields in rule_checks:
        voucher = _vouching_check(check, fields, grounded)
        vouched.append((voucher, fields, check.passed and not voucher.passed))

    # Which fields a violated invariant actually indicts. Fields a *passing* rule
    # corroborates are set aside; of the rest, the smallest explanations for every
    # violation are blamed. `None` means the fault could not be narrowed, and the
    # conservative reading — every field the violated rules reference — stands.
    violations = [fields for c, fields, downgraded in vouched if not c.passed and not downgraded]
    cleared = set().union(*[f for c, f, _ in vouched if c.passed]) if vouched else set()
    blamed = _localize_fault(violations, cleared)
    if blamed is None:
        blamed = set().union(*violations) if violations else set()

    results: list[VerifiedField] = []
    for v, grounding in zip(values, groundings, strict=True):
        # Gate-level integrity checks on the value itself, before any arithmetic.
        integrity: list[Check] = []
        agreement = raw_number_agreement(v)
        if agreement is not None:
            integrity.append(agreement)
        if counts[v.field] > 1:
            integrity.append(_duplicate_check(v.field, counts[v.field]))

        # every arithmetic rule that references this field, with a failure the
        # localization exonerated it from recorded rather than held against it
        arithmetic = [
            check
            if (check.passed or downgraded or v.field in blamed)
            else _exonerated_check(check.name, blamed & fields)
            for check, fields, downgraded in vouched
            if v.field in fields
        ]
        if not arithmetic and (value_carries_number(v) or v.field in covered):
            # FAIL-CLOSED: a number nothing checked is unverified, not "fine".
            # This is also what keeps the all() below from passing vacuously.
            #
            # ``v.field in covered`` is the second half of that, and it does not
            # ask what the value looks like. A rule names this field, so an
            # invariant was supposed to decide it; arriving here means none ran.
            # Digits are the usual reason a value gets checked, but they are not
            # the only way to write a quantity — 한글/한자 수사 ("일백만원",
            # "壹百萬"), Roman numerals and superscripts carry no ASCII digit and
            # some are stripped as notation before the matcher ever sees them.
            # Read as "text", such a value inherited a 상호's rules: grounded
            # verbatim, nothing else asked. Put the supply amount in the VAT slot
            # spelled 일백만원 and it verified at confidence 1.0. What the gate
            # promises about a field it named cannot depend on the notation the
            # document happened to use.
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
