"""Tests for the NumHall-KR benchmark harness.

Invariant-based (no hardcoded counts): they pass on the six hand-written
goldens alone AND after ``bench/generate_golden.py`` has expanded the set with
synthetic ``gen_*.json`` docs. The headline safety properties are pinned:
every labeled-bad value is caught (recall = 100%), nothing bad survives the
gate (post-gate NHR = 0), and the accept-everything baseline equals the
labeled bad-rate computed from the loaded set itself.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path

from groundextract.bench import compute_metrics, load_golden_dir, main, run_bench
from groundextract.models import Verdict

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "bench" / "golden"
RULES = ROOT / "rules"

# Hand-written clean goldens that must always be present and fully verified.
CLEAN_MANUAL_DOCS = (
    "tax_clean_01",
    "tax_clean_02",
    "statement_clean_01",
    "balance_clean_01",
)


@cache
def _bench():
    docs = load_golden_dir(GOLDEN)
    outcomes = run_bench(docs, RULES)
    return docs, outcomes, compute_metrics(outcomes)


def test_golden_set_loads():
    docs, _, m = _bench()
    assert len(docs) >= 6  # the six hand-written goldens are the floor
    ids = [d.doc_id for d in docs]
    assert len(ids) == len(set(ids))  # doc_ids stay unique across gen_*/manual
    assert all(d.fields for d in docs)
    # every shipped rule pack is exercised, and nothing else is — a pack the
    # benchmark never runs is a pack whose regressions nobody would notice, and a
    # doc_type with no pack would be scored with no arithmetic behind it
    shipped = {p.stem for p in RULES.glob("*.yaml")}
    assert {d.doc_type for d in docs} == shipped
    assert m["total_fields"] == sum(len(d.fields) for d in docs)


def test_gate_catches_every_hallucination():
    _, _, m = _bench()
    # No bad value slips through: recall = 100%, post-gate hallucination rate = 0.
    assert m["auto_discard_recall"] == 1.0
    assert m["numeric_hallucination_rate_after"] == 0.0
    assert m["confusion"]["fn"] == 0


def test_before_gate_baseline_matches_bad_rate():
    docs, _, m = _bench()
    total = sum(len(d.fields) for d in docs)
    bad = sum(1 for d in docs for f in d.fields if f.expected is Verdict.DISCARDED)
    assert bad > 0  # the set must actually contain labeled hallucinations
    assert m["ground_truth_bad"] == bad
    assert m["numeric_hallucination_rate_before"] == round(bad / total, 4)


def test_clean_manual_docs_stay_fully_verified():
    _, outcomes, _ = _bench()
    by_doc: dict[str, list] = {}
    for o in outcomes:
        by_doc.setdefault(o.doc_id, []).append(o)
    for clean in CLEAN_MANUAL_DOCS:
        assert clean in by_doc  # the hand-written goldens must never be removed
        assert all(o.predicted is Verdict.VERIFIED for o in by_doc[clean])


def test_over_discard_is_measured_as_precision_loss():
    # The gate taints sibling fields when an arithmetic invariant breaks, so
    # precision is intentionally below 1.0 (a documented v0.2 target). This
    # test pins the behaviour so a future fault-localization change is visible.
    _, _, m = _bench()
    assert m["confusion"]["fp"] > 0
    assert m["auto_discard_precision"] < 1.0


# --- generator (bench/generate_golden.py) --------------------------------------


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_golden", ROOT / "bench" / "generate_golden.py"
    )
    module = importlib.util.module_from_spec(spec)
    # register before exec so dataclasses can resolve postponed annotations
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generator_is_deterministic_and_gate_safe(tmp_path):
    gen = _load_generator()
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    kwargs = dict(count_tax=4, count_stmt=2, count_balance=2, seed=7)
    stats = gen.generate(**kwargs, out_dir=out_a)
    gen.generate(**kwargs, out_dir=out_b)

    names = sorted(p.name for p in out_a.glob("gen_*.json"))
    assert names == sorted(p.name for p in out_b.glob("gen_*.json"))
    assert names  # something was generated
    for name in names:  # same seed -> byte-identical output
        a = (out_a / name).read_text(encoding="utf-8")
        b = (out_b / name).read_text(encoding="utf-8")
        assert a == b

    docs = load_golden_dir(out_a)
    m = compute_metrics(run_bench(docs, RULES))
    assert m["auto_discard_recall"] == 1.0
    assert m["numeric_hallucination_rate_after"] == 0.0
    assert m["ground_truth_bad"] == stats["bad_fields"]
    assert stats["field_swap"] > 0  # arithmetic-only catches are always included


# --- an empty run is an error, not a perfect score -----------------------------
#
# Every rate here is a ratio whose denominator is the label count, so measuring
# nothing produced a flawless scorecard: 100% recall, 100% grounded-accuracy, a
# 0% post-gate hallucination rate — and exit 0, so a typo'd path in CI passed.
# These numbers are published as part of the project's public contract, so the
# empty case has to be loud.


def test_missing_golden_directory_is_an_error(tmp_path, capsys):
    code = main([str(tmp_path / "does_not_exist")])
    assert code != 0
    err = capsys.readouterr().err
    assert "not found" in err


def test_empty_golden_directory_is_an_error(tmp_path, capsys):
    code = main([str(tmp_path)])
    assert code != 0
    err = capsys.readouterr().err
    assert "no golden documents" in err
    assert "zero labels" in err


def test_documents_without_labeled_fields_are_an_error(tmp_path, capsys):
    (tmp_path / "empty_doc.json").write_text(
        json.dumps({"doc_id": "d", "doc_type": "tax_invoice", "full_text": "x", "fields": []}),
        encoding="utf-8",
    )
    code = main([str(tmp_path)])
    assert code != 0
    assert "no labeled" in capsys.readouterr().err


def test_a_real_golden_set_still_reports_and_succeeds(capsys):
    code = main([str(GOLDEN)])
    assert code == 0
    out = capsys.readouterr().out
    assert "NumHall-KR benchmark" in out
    assert "json:" in out


def test_published_numbers_match_the_measured_ones():
    """The README tables and the demo page must not drift from the benchmark.

    They already did: the live viewer shipped v0.1.1 figures (19.9% / 64.4% /
    35.8%) for two releases while both READMEs quoted v0.1.2's, so the page the
    README linked to as evidence contradicted it. These numbers are the public
    contract (see CHANGELOG.md), and hand-copying them into four files is exactly
    the kind of thing that goes stale quietly.
    """
    _, _, m = _bench()
    pct = lambda v: f"{v * 100:.1f}%"  # noqa: E731 - same format the docs use
    published = {
        "grounded_accuracy": pct(m["grounded_accuracy"]),
        "auto_discard_precision": pct(m["auto_discard_precision"]),
        "auto_discard_recall": pct(m["auto_discard_recall"]),
        "nhr_before": pct(m["numeric_hallucination_rate_before"]),
    }
    fields = str(m["total_fields"])

    for name in ("README.md", "README.ko.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for key, value in published.items():
            assert value in text, f"{name} does not quote {key} = {value}"
        assert fields in text, f"{name} does not quote total_fields = {fields}"

    # The viewer hardcodes the same figures in its benchmark card; recall is
    # rendered as "100%" there rather than "100.0%".
    viewer = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")
    for key in ("grounded_accuracy", "auto_discard_precision", "nhr_before"):
        assert published[key] in viewer, f"viewer/index.html does not quote {key}"
    assert fields in viewer


def test_readme_signature_case_still_matches_the_golden_document():
    """The README's worked example must be a document that actually exists.

    Both READMEs quote a benchmark document by filename, with its figures and the
    exact rule-violation lines, as *the* illustration of "only arithmetic sees
    it". Regenerating the golden set reshuffles the seeded stream, so those
    figures silently became someone else's document once already — the file the
    READMEs named ended up with no injected error at all.
    """
    doc_id = "gen_tax_019"
    payload = json.loads((GOLDEN / f"{doc_id}.json").read_text(encoding="utf-8"))
    injected = payload["injected_error"]
    assert injected == {"kind": "field-swap", "field": "vat"}, injected

    by_field = {f["field"]: f for f in payload["fields"]}
    vat, supply, total = by_field["vat"], by_field["supply"], by_field["total"]
    # the swapped value really is printed in the document (grounding must pass)
    assert vat["raw"] == supply["raw"]
    assert vat["grounding_quote"] in payload["full_text"]

    # and fault localization must still name vat alone, which is what the prose says
    assert vat["expected_verdict"] == "discarded"
    for survivor in ("supply", "total", "item1_supply", "item2_supply"):
        assert by_field[survivor]["expected_verdict"] == "verified", survivor

    for name in ("README.md", "README.ko.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert f"{doc_id}.json" in text, f"{name} names a different golden document"
        for quoted in (vat["raw"], total["raw"], by_field["item1_supply"]["raw"]):
            assert quoted in text, f"{name} does not quote {quoted}"
