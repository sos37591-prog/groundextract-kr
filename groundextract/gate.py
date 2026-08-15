"""The verification gate: orchestrates grounding + arithmetic rules.

Pipeline (per document):

    extracted values ── grounding ─┐
                                   ├─► gate ─► VerifiedField[]
    rule pack ───────── arithmetic ┘

Gate policy (the core trust guarantee):

  * A field must be **grounded** (verbatim evidence exists) AND pass **all**
    arithmetic rules that reference it.
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

from collections.abc import Iterable
from typing import Any

from .grounding import ground_value, normalize_number_str
from .models import Check, ExtractedValue, Verdict, VerifiedField
from .rules import RulePack, evaluate_pack_with_fields

#: Check names the gate itself produces (rule packs supply all other names).
GROUNDING_CHECK = "grounding"
RULES_APPLIED_CHECK = "rules_applied"


def value_number(value: ExtractedValue) -> float | None:
    """Numeric reading of a value, or ``None`` when it is purely textual.

    ``ExtractedValue.number`` is optional (so is ``number`` in the MCP tool
    schema), so it is parsed from ``raw`` when absent: a caller that leaves the
    optional field empty must not thereby escape arithmetic verification. The
    parse is the deterministic one grounding already applies to decide whether
    a value is a number, keeping both halves of the gate on one definition.
    """
    if value.number is not None:
        return value.number
    key = normalize_number_str(value.raw)
    if key is None:
        return None
    try:
        return float(key)
    except ValueError:  # pragma: no cover - normalize_number_str output is float-safe
        return None


def _build_env(values: Iterable[ExtractedValue]) -> dict[str, float]:
    """Field-name -> number map for the rule engine (numeric fields only)."""
    env: dict[str, float] = {}
    for v in values:
        number = value_number(v)
        if number is not None:
            env[v.field] = number
    return env


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
    env = _build_env(values)

    # Arithmetic checks are computed once over the whole field set, then
    # attributed to each field the rule references.
    rule_checks: list[tuple[Check, set[str]]] = (
        evaluate_pack_with_fields(rule_pack, env) if rule_pack is not None else []
    )

    results: list[VerifiedField] = []
    for v in values:
        grounding = ground_value(v.raw, v.grounding_quote, full_text)

        # every arithmetic rule that references this field
        arithmetic = [check for check, fields in rule_checks if v.field in fields]
        if not arithmetic and value_number(v) is not None:
            # FAIL-CLOSED: a number nothing checked is unverified, not "fine".
            # This is also what keeps the all() below from passing vacuously.
            arithmetic.append(_unverified_check(v, rule_pack))

        passed = grounding.passed and all(c.passed for c in arithmetic)

        results.append(
            VerifiedField(
                field=v.field,
                value=v,
                checks=[grounding, *arithmetic],
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
    applied = {
        c.name
        for f in fields
        for c in f.checks
        if c.name not in (GROUNDING_CHECK, RULES_APPLIED_CHECK)
    }
    pack_name = rule_pack.doc_type if isinstance(rule_pack, RulePack) else rule_pack
    return {
        "total": len(fields),
        "verified": verified,
        "discarded": discarded,
        "ungrounded": ungrounded,
        "rule_pack": pack_name,
        "rules_applied": len(applied),
    }
