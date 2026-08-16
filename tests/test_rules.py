"""Rule-pack loading is validated at load time, and fails by naming the problem.

The rule pack is the one input to this library a *user* writes by hand, so a
typo in it is the most likely error anyone will hit. It used to be accepted
without a single check: `load_rule_pack` built whatever the YAML said, and a
malformed expression surfaced much later as a bare

    SyntaxError: invalid syntax (<unknown>, line 1)

raised out of `ast.parse` inside `run_gate` — naming neither the file, nor the
rule, nor the expression. These tests pin the diagnosis, not just the rejection:
each asserts that the message identifies what to go and fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from groundextract import ExtractedValue, Verdict, available_doc_types, load_pack, run_gate
from groundextract.rules import Rule, RulePack, RulePackError, load_rule_pack

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def _pack_file(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "pack.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def _equals(**over: object) -> dict:
    rule = {"name": "r", "type": "equals", "lhs": {"field": "a"}, "rhs": {"field": "b"}}
    rule.update(over)
    return rule


# --- the packs that ship must keep loading ------------------------------------


@pytest.mark.parametrize("doc_type", ["tax_invoice", "statement", "balance_sheet"])
def test_bundled_packs_validate(doc_type):
    pack = load_pack(doc_type)
    assert pack.doc_type == doc_type
    assert pack.rules


def test_available_doc_types_matches_the_rules_directory():
    on_disk = {p.stem for p in RULES_DIR.glob("*.yaml")}
    assert set(available_doc_types()) == on_disk


# --- every way a hand-written pack goes wrong ---------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(
            {"rules": [_equals(rhs={"expr": "b + + *"})]},
            "cannot parse expr",
            id="expr-syntax-error",
        ),
        pytest.param(
            {"rules": [_equals(rhs={"expr": "__import__('os').system('x')"})]},
            "unsupported element",
            id="expr-not-arithmetic",
        ),
        pytest.param(
            {"rules": [_equals(rhs={"expr": ""})]},
            "non-empty string",
            id="expr-empty",
        ),
        pytest.param(
            {"rules": [{"name": "r", "type": "magic", "lhs": {"field": "a"}}]},
            "unknown rule type",
            id="unknown-type",
        ),
        pytest.param(
            {"rules": [{"name": "r", "type": "equals", "rhs": {"field": "b"}}]},
            "needs both 'lhs' and 'rhs'",
            id="missing-side",
        ),
        pytest.param(
            {"rules": [_equals(rhs={"field": "b", "expr": "a + 1"})]},
            "exactly one of",
            id="ambiguous-side",
        ),
        pytest.param(
            {"rules": [_equals(rhs={})]},
            "exactly one of",
            id="empty-side",
        ),
        pytest.param(
            {"rules": [{"name": "r", "type": "sum", "target": "a"}]},
            "non-empty 'items'",
            id="sum-without-items",
        ),
        pytest.param(
            {"rules": [{"name": "r", "type": "sum", "items": ["a"]}]},
            "needs a 'target'",
            id="sum-without-target",
        ),
        pytest.param(
            {"rules": [_equals(tol="약간")]},
            "'tol' must be a number",
            id="non-numeric-tol",
        ),
        pytest.param(
            {"rules": [_equals(tolerance=1)]},
            "unexpected keyword argument",
            id="misspelled-key",
        ),
        pytest.param(
            {"rules": [_equals(), _equals()]},
            "duplicate rule name",
            id="duplicate-name",
        ),
        pytest.param({"rules": {"a": 1}}, "'rules' must be a list", id="rules-not-a-list"),
        pytest.param({"rules": ["nope"]}, "expected a mapping", id="rule-not-a-mapping"),
        pytest.param({"doc_type": "", "rules": []}, "'doc_type' must be", id="empty-doc-type"),
        pytest.param(["a", "b"], "expected a mapping at the top level", id="top-level-list"),
    ],
)
def test_malformed_pack_is_rejected_with_a_useful_message(tmp_path, data, expected):
    path = _pack_file(tmp_path, data)
    with pytest.raises(RulePackError) as excinfo:
        load_rule_pack(path)
    message = str(excinfo.value)
    assert expected in message
    # the diagnosis must say *where*, or the user is back to guessing
    assert path.name in message


def test_invalid_yaml_is_rejected(tmp_path):
    path = tmp_path / "pack.yaml"
    path.write_text("doc_type: x\nrules: [ unclosed", encoding="utf-8")
    with pytest.raises(RulePackError, match="invalid YAML"):
        load_rule_pack(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(RulePackError, match="cannot read rule pack"):
        load_rule_pack(tmp_path / "nope.yaml")


# --- a pack built in code bypasses the loader, so the gate must still hold ----


def test_a_malformed_rule_built_in_code_fails_closed_instead_of_raising():
    # load_rule_pack is the guarded door, but RulePack is a plain dataclass.
    # A rule the engine cannot even read must discard the field, not crash the
    # caller with a traceback from ast.parse.
    pack = RulePack(
        doc_type="hand_made",
        rules=[Rule(name="broken", type="equals", lhs={"field": "a"}, rhs={"expr": "b + + *"})],
    )
    fields = run_gate([ExtractedValue("a", "100원", 100, "a 100원")], "a 100원", pack)
    assert fields[0].verdict is Verdict.DISCARDED
    assert any("malformed rule" in c.detail for c in fields[0].checks)
