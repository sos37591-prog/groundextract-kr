"""NumHall-KR benchmark harness: measure how well the gate kills bad numbers.

Loads golden documents (each field labeled with its ground-truth verdict:
``verified`` for a correct extraction, ``discarded`` for a hallucinated/wrong
one), runs the verification gate, and reports:

  * Numeric Hallucination Rate (NHR) - of the values the gate KEPT, the fraction
    that were actually bad (hallucinations that slipped through). Lower is
    better. Reported before-gate (accept everything) vs after-gate.
  * Grounded-Accuracy - overall fraction of fields whose gate verdict matches
    the ground-truth verdict.
  * Auto-Discard Precision / Recall - treating DISCARDED as the positive class:
    precision = of gate-discarded fields, how many were truly bad;
    recall    = of truly bad fields, how many the gate discarded.

Deterministic and key-free: golden files carry the extracted values + cited
quotes directly, so no LLM/OCR is needed to run the benchmark. This is the
"benchmark-first" pillar (cf. AutoRAG): reproducible metrics, not a one-off demo.

Run:  python -m groundextract.bench [golden_dir]
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .gate import run_gate
from .models import ExtractedValue, Verdict
from .rules import RulePack, load_rule_pack


@dataclass
class GoldenField:
    """One labeled field: the extracted value plus its ground-truth verdict."""

    field: str
    raw: str
    number: float | None
    grounding_quote: str | None
    expected: Verdict  # VERIFIED = a correct extraction; DISCARDED = a bad one


@dataclass
class GoldenDoc:
    doc_id: str
    doc_type: str
    full_text: str
    fields: list[GoldenField]


def load_golden_doc(path: str | Path) -> GoldenDoc:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = [
        GoldenField(
            field=f["field"],
            raw=f["raw"],
            number=f.get("number"),
            grounding_quote=f.get("grounding_quote"),
            expected=Verdict(f["expected_verdict"]),
        )
        for f in data["fields"]
    ]
    return GoldenDoc(data["doc_id"], data["doc_type"], data["full_text"], fields)


def load_golden_dir(dir_path: str | Path) -> list[GoldenDoc]:
    return [load_golden_doc(p) for p in sorted(Path(dir_path).glob("*.json"))]


def _rule_pack_for(doc_type: str, rules_dir: Path) -> RulePack | None:
    p = rules_dir / f"{doc_type}.yaml"
    return load_rule_pack(p) if p.exists() else None


@dataclass
class FieldOutcome:
    doc_id: str
    field: str
    expected: Verdict
    predicted: Verdict


def run_bench(docs: Iterable[GoldenDoc], rules_dir: str | Path) -> list[FieldOutcome]:
    """Run the gate on every golden doc; return per-field expected vs predicted."""
    rules_dir = Path(rules_dir)
    outcomes: list[FieldOutcome] = []
    for doc in docs:
        pack = _rule_pack_for(doc.doc_type, rules_dir)
        values = [
            ExtractedValue(f.field, f.raw, f.number, f.grounding_quote) for f in doc.fields
        ]
        predicted = {vf.field: vf.verdict for vf in run_gate(values, doc.full_text, pack)}
        for f in doc.fields:
            outcomes.append(FieldOutcome(doc.doc_id, f.field, f.expected, predicted[f.field]))
    return outcomes


def compute_metrics(outcomes: list[FieldOutcome]) -> dict:
    """Confusion matrix + headline metrics (DISCARDED is the positive class)."""
    D, V = Verdict.DISCARDED, Verdict.VERIFIED
    tp = sum(1 for o in outcomes if o.expected is D and o.predicted is D)
    fp = sum(1 for o in outcomes if o.expected is V and o.predicted is D)
    fn = sum(1 for o in outcomes if o.expected is D and o.predicted is V)
    tn = sum(1 for o in outcomes if o.expected is V and o.predicted is V)
    total = len(outcomes)
    bad = tp + fn                       # ground-truth bad values
    kept = fn + tn                      # values the gate KEPT (predicted verified)

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    accuracy = (tp + tn) / total if total else 1.0
    nhr_before = bad / total if total else 0.0     # accept-everything baseline
    nhr_after = fn / kept if kept else 0.0         # bad values among those kept

    return {
        "total_fields": total,
        "ground_truth_bad": bad,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "numeric_hallucination_rate_before": round(nhr_before, 4),
        "numeric_hallucination_rate_after": round(nhr_after, 4),
        "grounded_accuracy": round(accuracy, 4),
        "auto_discard_precision": round(precision, 4),
        "auto_discard_recall": round(recall, 4),
    }


def format_report(metrics: dict) -> str:
    m, c = metrics, metrics["confusion"]
    return "\n".join(
        [
            "=== NumHall-KR benchmark ===",
            f"fields: {m['total_fields']}  (ground-truth bad: {m['ground_truth_bad']})",
            f"confusion: TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']}",
            f"Numeric Hallucination Rate: {m['numeric_hallucination_rate_before']:.1%} "
            f"(before gate)  ->  {m['numeric_hallucination_rate_after']:.1%} (after gate)",
            f"Grounded-Accuracy:          {m['grounded_accuracy']:.1%}",
            f"Auto-Discard Precision:     {m['auto_discard_precision']:.1%}",
            f"Auto-Discard Recall:        {m['auto_discard_recall']:.1%}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark. Returns a process exit code.

    Zero golden documents is an error, not a perfect score. Every rate here is
    a ratio whose denominator is the label count, so an empty (or mistyped)
    directory reports 100% recall, 100% grounded-accuracy and a 0% hallucination
    rate — a flawless scorecard produced by measuring nothing. These numbers are
    published, so the failure mode has to be loud and the exit code non-zero.
    """
    args = argv if argv is not None else sys.argv[1:]
    root = Path(__file__).resolve().parent.parent
    golden_dir = Path(args[0]) if args else root / "bench" / "golden"

    if not golden_dir.is_dir():
        print(f"error: golden directory not found: {golden_dir}", file=sys.stderr)
        return 2

    docs = load_golden_dir(golden_dir)
    if not docs:
        print(
            f"error: no golden documents (*.json) in {golden_dir}\n"
            "       refusing to report metrics computed over zero labels.",
            file=sys.stderr,
        )
        return 2

    metrics = compute_metrics(run_bench(docs, root / "rules"))
    if metrics["total_fields"] == 0:
        print(
            f"error: {len(docs)} golden document(s) in {golden_dir} carry no labeled "
            "fields;\n       refusing to report metrics computed over zero labels.",
            file=sys.stderr,
        )
        return 2

    print(format_report(metrics))
    print("json:", json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
