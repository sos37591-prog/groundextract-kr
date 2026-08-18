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
import dataclasses
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


class RulePackError(ValueError):
    """A rule pack is malformed. Raised by :func:`load_rule_pack`.

    Validation happens at load time on purpose. The rule pack is the one input
    to this library a *user* writes by hand, so a typo in it is the most likely
    error anyone will hit — and the failure has to name the file and the rule,
    not surface as a bare ``SyntaxError`` from deep inside rule evaluation long
    after the pack was loaded.
    """


def _parse_expr(expr: str, where: str) -> ast.Expression:
    """Parse an expression and reject anything the safe evaluator cannot run."""
    if not isinstance(expr, str) or not expr.strip():
        raise RulePackError(f"{where}: 'expr' must be a non-empty string")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise RulePackError(f"{where}: cannot parse expr {expr!r} ({e.msg})") from e
    for node in ast.walk(tree):
        # ast.Load is the context every Name carries; it is structure, not syntax.
        if isinstance(node, (ast.Expression, ast.Name, ast.Load)):
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            continue
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            continue
        if isinstance(node, tuple(_BIN_OPS) + tuple(_UNARY_OPS)):
            continue
        raise RulePackError(
            f"{where}: expr {expr!r} uses an unsupported element "
            f"({type(node).__name__}); only + - * / and field names are allowed"
        )
    return tree


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


#: Rule types the engine can evaluate. A pack naming anything else is a typo,
#: and saying so at load time beats discovering it as a failed check per field.
RULE_TYPES = ("equals", "sum")

#: Ways a rule side can name a value.
_SIDE_KEYS = ("field", "expr", "const")


def _validate_side(side: object, where: str) -> None:
    if not isinstance(side, dict):
        raise RulePackError(f"{where}: expected a mapping like {{field: supply}}, got {side!r}")
    present = [k for k in _SIDE_KEYS if k in side]
    if len(present) != 1:
        raise RulePackError(
            f"{where}: needs exactly one of {', '.join(_SIDE_KEYS)}; got "
            f"{sorted(side) or 'nothing'}"
        )
    key = present[0]
    if key == "expr":
        _parse_expr(side["expr"], where)
    elif key == "field" and not isinstance(side["field"], str):
        raise RulePackError(f"{where}: 'field' must be a field name, got {side['field']!r}")
    elif key == "const" and not isinstance(side["const"], (int, float)):
        raise RulePackError(f"{where}: 'const' must be a number, got {side['const']!r}")


def _validate_rule(rule: Rule, where: str) -> None:
    if not isinstance(rule.name, str) or not rule.name:
        raise RulePackError(f"{where}: 'name' must be a non-empty string")
    if rule.type not in RULE_TYPES:
        raise RulePackError(
            f"{where}: unknown rule type {rule.type!r}; expected one of {', '.join(RULE_TYPES)}"
        )
    if not isinstance(rule.tol, (int, float)) or isinstance(rule.tol, bool):
        raise RulePackError(f"{where}: 'tol' must be a number, got {rule.tol!r}")

    if rule.type == "equals":
        for name in ("lhs", "rhs"):
            side = getattr(rule, name)
            if side is None:
                raise RulePackError(f"{where}: an 'equals' rule needs both 'lhs' and 'rhs'")
            _validate_side(side, f"{where}.{name}")
    else:  # sum
        if not isinstance(rule.target, str) or not rule.target:
            raise RulePackError(f"{where}: a 'sum' rule needs a 'target' field name")
        if not isinstance(rule.items, list) or not rule.items:
            raise RulePackError(f"{where}: a 'sum' rule needs a non-empty 'items' list")
        bad = [i for i in rule.items if not isinstance(i, str)]
        if bad:
            raise RulePackError(f"{where}: 'items' must be field names; got {bad!r}")


def load_rule_pack(path: str | Path) -> RulePack:
    """Load and validate a YAML rule pack from disk.

    Raises :class:`RulePackError` — naming the file and the offending rule — for
    anything the engine could not evaluate later.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RulePackError(f"cannot read rule pack {path}: {e}") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise RulePackError(f"{path}: invalid YAML ({e})") from e

    if not isinstance(data, dict):
        raise RulePackError(
            f"{path}: expected a mapping at the top level, got {type(data).__name__}"
        )
    doc_type = data.get("doc_type", "unknown")
    if not isinstance(doc_type, str) or not doc_type:
        raise RulePackError(f"{path}: 'doc_type' must be a non-empty string")

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RulePackError(f"{path}: 'rules' must be a list, got {type(raw_rules).__name__}")

    rules: list[Rule] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_rules):
        where = f"{path}: rules[{i}]"
        if not isinstance(item, dict):
            raise RulePackError(f"{where}: expected a mapping, got {type(item).__name__}")
        try:
            rule = Rule(**item)
        except TypeError as e:
            known = ", ".join(f.name for f in dataclasses.fields(Rule))
            raise RulePackError(f"{where}: {e}; known keys are {known}") from e
        _validate_rule(rule, f"{path}: rule {rule.name!r}")
        if rule.name in seen:
            raise RulePackError(f"{path}: duplicate rule name {rule.name!r}")
        seen.add(rule.name)
        rules.append(rule)

    return RulePack(doc_type=doc_type, rules=rules)


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
        # load_rule_pack rejects a malformed rule up front, but a RulePack can
        # also be built in code, so a rule that cannot even be *read* must fail
        # closed and visibly rather than raising out of the gate.
        try:
            referenced = _referenced_fields(rule)
        except (SyntaxError, ValueError, TypeError) as e:
            # Which fields the rule meant to check is exactly what could not be
            # read, so the failure is attributed to every numeric field: a pack
            # this broken has verified nothing, and saying so on each field beats
            # a warning attached to none of them.
            evaluated.append(
                (
                    Check(
                        name=getattr(rule, "name", "<unnamed rule>") or "<unnamed rule>",
                        passed=False,
                        detail=(
                            f"malformed rule; cannot determine which fields it checks ({e}). "
                            "Load the pack with load_rule_pack() to have it validated."
                        ),
                    ),
                    set(env),
                )
            )
            continue
        if _rule_applies_to(referenced, env):
            evaluated.append((evaluate_rule(rule, env), referenced))
    return evaluated


def evaluate_pack(pack: RulePack, env: dict[str, float]) -> list[Check]:
    """Evaluate every rule that applies to this document (checks only)."""
    return [check for check, _fields in evaluate_pack_with_fields(pack, env)]


def pack_fields(pack: RulePack) -> frozenset[str]:
    """Every field name the pack's rules mention.

    The gate needs this to tell "no invariant covers this field" apart from "an
    invariant covers it but could not run". The first is a documented gap; the
    second is a value that was supposed to be checked and was not, which has to
    fail closed however the value happens to be spelled.

    ``load_rule_pack`` is the guarded door, but ``RulePack`` is a plain dataclass
    a caller can build by hand, so a rule here may carry an expression that does
    not parse. Such a rule is skipped rather than raised on: reading the pack must
    not be the thing that crashes.

    Skipping is not free, and the limit is worth stating plainly. The fields that
    rule alone named are then missing from this set, so the gate does not know an
    invariant was meant to cover them. A *numeric* value is still discarded —
    :func:`evaluate_pack_with_fields` blames every field in the environment for a
    malformed rule, and the gate's own fail-closed branch catches anything holding
    digits. A value carrying no readable number in a field named only by the
    broken rule is the gap: nothing claims it, so grounding alone decides it.

    Loading from YAML cannot reach that state — the loader rejects an unparseable
    expression outright — so it takes a hand-built pack with a rule the author
    never validated.
    """
    fields: set[str] = set()
    for rule in pack.rules:
        try:
            fields |= _referenced_fields(rule)
        except (SyntaxError, ValueError, TypeError):
            continue
    return frozenset(fields)


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
    return _rule_applies_to(_referenced_fields(rule), env)


def _rule_applies_to(ref: set[str], env: dict[str, float]) -> bool:
    # Apply only when the rule is fully evaluable (all fields present). This
    # keeps optional invariants (e.g. per-item sums) from failing when those
    # line items simply weren't part of the extraction.
    #
    # NOTE: "no rule applied" must never be read as "nothing was wrong". The
    # gate treats a numeric value that no rule reached as UNVERIFIED and
    # discards it (fail-closed); this filter only decides what gets *run*.
    return bool(ref) and ref.issubset(env)
