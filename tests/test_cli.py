"""`verify`: the gate over files you supply, and the exit code a pipeline reads.

The library and the MCP server could already verify an arbitrary extraction, but
both ask you to write code first. `verify` closes that gap — and because a
pipeline is expected to branch on it, the exit code is part of the contract:
0 when every field survived, 1 when anything was discarded, 2 for usage errors.

The bare command must keep running the demo, so the dispatch is tested too.
"""

from __future__ import annotations

import json

import pytest

from groundextract.__main__ import main

DOC = "전자세금계산서\n공급가액  10,000,000원\n세액  1,000,000원\n합계금액  11,000,000원\n"
GOOD = [
    {"field": "supply", "raw": "10,000,000원", "number": 10_000_000},
    {"field": "vat", "raw": "1,000,000원", "number": 1_000_000},
    {"field": "total", "raw": "11,000,000원", "number": 11_000_000},
]
HALLUCINATED = [
    {"field": "supply", "raw": "10,000,000원", "number": 10_000_000},
    {"field": "vat", "raw": "1,500,000원", "number": 1_500_000},  # nowhere in DOC
    {"field": "total", "raw": "11,000,000원", "number": 11_000_000},
]


@pytest.fixture
def inputs(tmp_path):
    def write(values, text=DOC):
        doc = tmp_path / "doc.txt"
        doc.write_text(text, encoding="utf-8")
        vals = tmp_path / "values.json"
        vals.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        return ["--doc", str(doc), "--values", str(vals)]

    return write


# --- the exit code is the contract --------------------------------------------


def test_a_clean_extraction_exits_zero(inputs, capsys):
    code = main(["verify", *inputs(GOOD), "--doc-type", "tax_invoice"])
    assert code == 0
    assert "verified" in capsys.readouterr().out


def test_a_discarded_field_exits_one(inputs, capsys):
    # `... verify && ingest` must not ingest.
    code = main(["verify", *inputs(HALLUCINATED), "--doc-type", "tax_invoice"])
    assert code == 1
    assert "discarded" in capsys.readouterr().out


def test_json_output_carries_fields_and_summary(inputs, capsys):
    code = main(["verify", *inputs(HALLUCINATED), "--doc-type", "tax_invoice", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["summary"]["discarded"] == 2
    by_field = {f["field"]: f for f in payload["fields"]}
    assert by_field["vat"]["verdict"] == "discarded"
    assert by_field["vat"]["confidence"] == 0.0


# --- your own rule pack --------------------------------------------------------


def test_a_pack_of_your_own_is_accepted(tmp_path, inputs, capsys):
    pack = tmp_path / "payroll.yaml"
    pack.write_text(
        "doc_type: payroll\n"
        "rules:\n"
        "  - name: net_equals_gross_minus_deductions\n"
        "    type: equals\n"
        "    lhs: {field: net_pay}\n"
        "    rhs: {expr: 'gross_pay - deductions'}\n"
        "    tol: 1.0\n",
        encoding="utf-8",
    )
    args = inputs(
        [
            {"field": "gross_pay", "raw": "4,500,000원"},
            {"field": "deductions", "raw": "541,800원"},
            {"field": "net_pay", "raw": "3,958,200원"},
        ],
        text="급여명세서\n총지급액  4,500,000원\n공제총액  541,800원\n실지급액  3,958,200원\n",
    )
    assert main(["verify", *args, "--rules", str(pack)]) == 0
    assert '"rule_pack": "payroll"' in capsys.readouterr().out


def test_a_broken_pack_is_reported_not_raised(tmp_path, inputs):
    pack = tmp_path / "broken.yaml"
    pack.write_text(
        "doc_type: x\nrules:\n  - name: r\n    type: equals\n"
        "    lhs: {field: a}\n    rhs: {expr: 'b + + *'}\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        main(["verify", *inputs(GOOD), "--rules", str(pack)])
    assert excinfo.value.code == 2


# --- usage errors exit 2, and say what to fix ----------------------------------


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        pytest.param([], "--doc-type", id="no-rule-pack"),
        pytest.param(["--doc-type", "세금계산서"], "invalid choice", id="unknown-doc-type"),
    ],
)
def test_usage_errors(inputs, capsys, extra, expected):
    with pytest.raises(SystemExit) as excinfo:
        main(["verify", *inputs(GOOD), *extra])
    assert excinfo.value.code == 2
    assert expected in capsys.readouterr().err


def test_a_missing_document_is_reported(tmp_path, inputs, capsys):
    args = inputs(GOOD)
    args[1] = str(tmp_path / "nope.txt")
    with pytest.raises(SystemExit) as excinfo:
        main(["verify", *args, "--doc-type", "tax_invoice"])
    assert excinfo.value.code == 2
    assert "cannot read" in capsys.readouterr().err


def test_an_empty_document_is_reported(inputs, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["verify", *inputs(GOOD, text="   \n"), "--doc-type", "tax_invoice"])
    assert excinfo.value.code == 2
    assert "nothing to ground against" in capsys.readouterr().err


def test_malformed_values_are_reported(tmp_path, capsys):
    doc = tmp_path / "doc.txt"
    doc.write_text(DOC, encoding="utf-8")
    vals = tmp_path / "values.json"
    vals.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["verify", "--doc", str(doc), "--values", str(vals), "--doc-type", "tax_invoice"])
    assert excinfo.value.code == 2
    assert "must be an array" in capsys.readouterr().err


# --- the bare command still runs the demo --------------------------------------


def test_the_demo_still_runs_without_arguments(capsys):
    assert main([]) == 0
    assert "GroundExtract-KR demo" in capsys.readouterr().out


def test_a_stray_file_argument_points_at_verify(capsys):
    with pytest.raises(SystemExit):
        main(["내문서.pdf"])
    assert "verify --doc" in capsys.readouterr().err
