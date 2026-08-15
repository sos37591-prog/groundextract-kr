"""Deterministic arithmetic rule engine + YAML rule-pack loader.

A rule pack is a small YAML file describing domain arithmetic invariants for a
document type (tax invoice, financial statement, ...). Each rule references
extracted fields by name and asserts an arithmetic relationship, e.g.

    - name: vat_equals_supply_x_10pct
      type: equals
      lhs: {field: vat}
      rhs: {expr: "supply * 0.10"}
      tol: 1.0

Supported rule ``type``s:

  * ``equals`` - lhs == rhs within absolute tolerance ``tol``.
  * ``sum``    - target field == sum of ``items`` fields (± tol).

Expressions are evaluated over field *numbers* with a tiny safe evaluator
(only +, -, *, /, parentheses and numeric field names). No ``eval`` of
arbitrary Python.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import Check

# --- locating the shipped rule packs ------------------------------------------


def default_rules_dir() -> Path:
    """Directory holding the bundled ``<doc_type>.yaml`` rule packs.

    Installed wheels carry the packs inside the package (see the
    ``force-include`` entry in pyproject.toml); a source checkout keeps them at
    the repository root. Checking the package copy first means
    ``pip install groundextract`` works without the repository present.
    """
    packaged = Path(__file__).resolve().parent / "rules"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parent.parent / "rules"


# --- safe arithmetic expression evaluator -------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left, env), _eval_node(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, env))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise KeyError(f"unknown field in expression: {node.id}")
        return env[node.id]
    raise ValueError(f"unsupported expression element: {ast.dump(node)}")


def eval_expr(expr: str, env: dict[str, float]) -> float:
    """Evaluate a restricted arithmetic expression over field numbers."""
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree, env)


# --- rule model ----------------------------------------------------------------


@dataclass
class Rule:
    name: str
    type: str
    tol: float = 1.0
    # equals
    lhs: dict[str, Any] | None = None
    rhs: dict[str, Any] | None = None
    # sum
    target: str | None = None
    items: list[str] | None = None


@dataclass
class RulePack:
    doc_type: str
    rules: list[Rule]


def load_rule_pack(path: str | Path) -> RulePack:
    """Load a YAML rule pack from disk."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = [Rule(**r) for r in data.get("rules", [])]
    return RulePack(doc_type=data.get("doc_type", "unknown"), rules=rules)


# --- evaluation ----------------------------------------------------------------


def _side_value(side: dict[str, Any], env: dict[str, float]) -> float:
    """Resolve a rule side: either {field: x} or {expr: "..."}."""
    if "field" in side:
        return env[side["field"]]
    if "expr" in side:
        return eval_expr(side["expr"], env)
    if "const" in side:
        return float(side["const"])
    raise ValueError(f"invalid rule side: {side}")


def evaluate_rule(rule: Rule, env: dict[str, float]) -> Check:
    """Evaluate one rule against the field-number environment.

    Missing fields => the rule cannot be evaluated => failed check (a value
    referenced by an invariant is absent, which is itself suspicious).
    """
    try:
        if rule.type == "equals":
            lhs = _side_value(rule.lhs or {}, env)
            rhs = _side_value(rule.rhs or {}, env)
            ok = abs(lhs - rhs) <= rule.tol
            # ,.0f (not :g) so large won amounts aren't rounded into scientific
            # notation that hides a small violation (e.g. a 100-won difference).
            detail = f"{lhs:,.0f} vs {rhs:,.0f} (tol {rule.tol:g}) -> diff {abs(lhs - rhs):,.0f}"
            return Check(name=rule.name, passed=ok, detail=detail)

        if rule.type == "sum":
            target = env[rule.target]  # type: ignore[index]
            total = sum(env[i] for i in (rule.items or []))
            ok = abs(target - total) <= rule.tol
            detail = (
                f"{rule.target}={target:,.0f} vs sum({'+'.join(rule.items or [])})"
                f"={total:,.0f} -> diff {abs(target - total):,.0f}"
            )
            return Check(name=rule.name, passed=ok, detail=detail)

        return Check(name=rule.name, passed=False, detail=f"unknown rule type: {rule.type}")
    except KeyError as e:
        return Check(name=rule.name, passed=False, detail=f"missing field {e}; cannot verify")
    except (ValueError, ZeroDivisionError) as e:  # malformed rule pack -> safe-fail, no crash
        return Check(name=rule.name, passed=False, detail=f"cannot evaluate rule ({e})")


def evaluate_pack_with_fields(
    pack: RulePack, env: dict[str, float]
) -> list[tuple[Check, set[str]]]:
    """Evaluate the applicable rules, pairing each Check with the fields it references.

    The gate needs both halves together — the verdict *and* which fields the
    rule reached — so they are produced in a single pass and cannot drift apart
    (an earlier version zipped two separately filtered lists).

    A rule applies only when *all* of its referenced fields are present in
    ``env`` (so an optional invariant like "supply == sum(items)" is simply not
    checked when the line items weren't extracted, rather than failing). A
    field that ends up with **no** applicable rule is not thereby trustworthy:
    see the fail-closed guard in :func:`groundextract.gate.run_gate`.
    """
    evaluated: list[tuple[Check, set[str]]] = []
    for rule in pack.rules:
        if _rule_applies(rule, env):
            evaluated.append((evaluate_rule(rule, env), _referenced_fields(rule)))
    return evaluated


def evaluate_pack(pack: RulePack, env: dict[str, float]) -> list[Check]:
    """Evaluate every rule that applies to this document (checks only)."""
    return [check for check, _fields in evaluate_pack_with_fields(pack, env)]


def _referenced_fields(rule: Rule) -> set[str]:
    fields: set[str] = set()
    for side in (rule.lhs, rule.rhs):
        if side and "field" in side:
            fields.add(side["field"])
        if side and "expr" in side:
            for node in ast.walk(ast.parse(side["expr"], mode="eval")):
                if isinstance(node, ast.Name):
                    fields.add(node.id)
    if rule.target:
        fields.add(rule.target)
    if rule.items:
        fields.update(rule.items)
    return fields


def _rule_applies(rule: Rule, env: dict[str, float]) -> bool:
    ref = _referenced_fields(rule)
    # Apply only when the rule is fully evaluable (all fields present). This
    # keeps optional invariants (e.g. per-item sums) from failing when those
    # line items simply weren't part of the extraction.
    #
    # NOTE: "no rule applied" must never be read as "nothing was wrong". The
    # gate treats a numeric value that no rule reached as UNVERIFIED and
    # discards it (fail-closed); this filter only decides what gets *run*.
    return bool(ref) and ref.issubset(env)
