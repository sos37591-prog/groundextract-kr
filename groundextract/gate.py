"""The verification gate: orchestrates grounding + arithmetic rules.

Pipeline (per document):

    extracted values ── grounding ─┐
                                   ├─► gate ─► VerifiedField[]
    rule pack ───────── arithmetic ┘

Gate policy (the core trust guarantee):

  * A field must be **grounded** (verbatim evidence exists) AND pass **all**
    arithmetic rules that reference it.
  * If grounding fails OR any referencing rule is violated, the field is
    **auto-discarded**: verdict = DISCARDED, ``confidence = 0``. This is what
    kills a hallucinated / rule-breaking number before it reaches downstream.
  * Otherwise verdict = VERIFIED and the field keeps its extractor confidence.
"""

from __future__ import annotations

from collections.abc import Iterable

from .grounding import ground_value
from .models import Check, ExtractedValue, Verdict, VerifiedField
from .rules import RulePack, evaluate_pack


def _build_env(values: Iterable[ExtractedValue]) -> dict[str, float]:
    """Field-name -> number map for the rule engine (numeric fields only)."""
    return {v.field: v.number for v in values if v.number is not None}


def run_gate(
    values: list[ExtractedValue],
    full_text: str,
    rule_pack: RulePack | None = None,
) -> list[VerifiedField]:
    """Verify every extracted value and return per-field verdicts."""
    env = _build_env(values)

    # Arithmetic checks are computed once over the whole field set, then
    # attributed to each field the rule references.
    rule_checks: list[tuple[Check, set[str]]] = []
    if rule_pack is not None:
        from .rules import _referenced_fields  # local import: internal helper

        pack_checks = evaluate_pack(rule_pack, env)
        applicable = _applicable_rules(rule_pack, env)
        for pack_check, rule in zip(pack_checks, applicable, strict=True):
            rule_checks.append((pack_check, _referenced_fields(rule)))

    results: list[VerifiedField] = []
    for v in values:
        checks: list[Check] = [ground_value(v.raw, v.grounding_quote, full_text)]

        # attach every arithmetic rule that references this field
        for check, fields in rule_checks:
            if v.field in fields:
                checks.append(check)

        grounded = checks[0].passed
        rules_ok = all(c.passed for c in checks[1:])
        passed = grounded and rules_ok

        results.append(
            VerifiedField(
                field=v.field,
                value=v,
                checks=checks,
                verdict=Verdict.VERIFIED if passed else Verdict.DISCARDED,
                # AUTO-DISCARD: any failure zeroes confidence, blocking downstream.
                confidence=v.confidence if passed else 0.0,
            )
        )
    return results


def _applicable_rules(rule_pack: RulePack, env: dict[str, float]):
    """Rules actually evaluated by ``evaluate_pack`` (same order/filter)."""
    from .rules import _rule_applies

    return [r for r in rule_pack.rules if _rule_applies(r, env)]


def summarize(fields: list[VerifiedField]) -> dict[str, int]:
    """Small report: counts for the CLI / bench / viewer."""
    verified = sum(1 for f in fields if f.verdict is Verdict.VERIFIED)
    discarded = len(fields) - verified
    ungrounded = sum(1 for f in fields if not f.grounded)
    return {
        "total": len(fields),
        "verified": verified,
        "discarded": discarded,
        "ungrounded": ungrounded,
    }
