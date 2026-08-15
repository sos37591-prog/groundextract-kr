"""Quick real-LLM check (no PDF): OllamaExtractor -> gate on a text invoice.

Run:  python bench/verify_llm_only.py [MODEL_TAG]
Default model is ``qwen2.5:7b`` (Apache-2.0). See
``groundextract/llm/openweight.py`` for why the 3B/72B Qwen2.5 sizes are
deliberately avoided (Qwen-Research = non-commercial).

Note: ``qwen2.5:1.5b`` is Apache-2.0 but returns label-only quotes here, so
every value is (correctly) discarded by the gate. Use 7B for real extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from groundextract.gate import run_gate, summarize  # noqa: E402
from groundextract.llm.openweight import OllamaExtractor  # noqa: E402
from groundextract.models import Verdict  # noqa: E402
from groundextract.rules import load_rule_pack  # noqa: E402

DOC = (
    "전자세금계산서\n"
    "승인번호: 20260701-41000000-12345678\n"
    "공급자: 주식회사 가온소프트\n"
    "공급받는자: 주식회사 나래상사\n"
    "공급가액  1,000,000원\n"
    "세액  100,000원\n"
    "합계금액  1,100,000원\n"
)


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
    ex = OllamaExtractor(model=model)
    values = ex.extract(DOC, doc_type="tax_invoice")
    print(f"=== REAL LLM EXTRACTION ({model} via Ollama) ===")
    for v in values:
        print(f"  {v.field:7} raw={v.raw!r} number={v.number} quote={v.grounding_quote!r}")
    if not values:
        print("FAIL: no values extracted")
        return 1

    fields = run_gate(values, DOC, load_rule_pack(ROOT / "rules" / "tax_invoice.yaml"))
    print("=== GATE ===")
    for f in fields:
        print(f"  {f.field:7} verdict={f.verdict.value} conf={f.confidence}")
        for c in f.failed_checks:
            print(f"      ! {c.name}: {c.detail}")
    print("summary:", summarize(fields))
    ok = all(f.verdict is Verdict.VERIFIED for f in fields)
    print("RESULT:", "PASS" if ok else "PARTIAL/FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
