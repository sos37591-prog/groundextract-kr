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
import sys
from functools import cache
from pathlib import Path

from groundextract.bench import compute_metrics, load_golden_dir, run_bench
from groundextract.models import Verdict

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "bench" / "golden"
RULES = ROOT / "rules"

# Hand-written clean goldens that must always be present and fully verified.
CLEAN_MANUAL_DOCS = ("tax_clean_01", "tax_clean_02", "statement_clean_01")


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
    assert all(d.doc_type in ("tax_invoice", "statement") for d in docs)
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
    stats = gen.generate(count_tax=4, count_stmt=2, seed=7, out_dir=out_a)
    gen.generate(count_tax=4, count_stmt=2, seed=7, out_dir=out_b)

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
