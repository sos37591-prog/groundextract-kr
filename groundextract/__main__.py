"""Tiny CLI demo: `python -m groundextract` runs the gate on a canned invoice.

Shows the core value proposition in one command with no keys: a hallucinated
VAT is auto-discarded (confidence 0) while grounded, arithmetic-consistent
numbers stay verified. Real PDF/LLM paths are TODO (see llm/base.py).
"""

from __future__ import annotations

import json

from .gate import run_gate, summarize
from .llm import MockExtractor
from .models import ExtractedValue
from .rules import default_rules_dir, load_rule_pack

_DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)


def main() -> None:
    pack = load_rule_pack(default_rules_dir() / "tax_invoice.yaml")

    # Two grounded/consistent values + one hallucinated VAT (250,000 is nowhere
    # in the document and breaks vat = supply * 10%).
    extractor = MockExtractor(
        [
            ExtractedValue("supply", "1,000,000원", 1_000_000, "공급가액  1,000,000원"),
            ExtractedValue("vat", "250,000원", 250_000, "세액  250,000원"),  # hallucinated
            ExtractedValue("total", "1,100,000원", 1_100_000, "합계금액  1,100,000원"),
        ]
    )

    fields = run_gate(extractor.extract(_DOC, doc_type="tax_invoice"), _DOC, pack)

    print("=== GroundExtract-KR demo ===")
    for f in fields:
        mark = "OK " if f.verdict.value == "verified" else "XX "
        print(
            f"{mark}{f.field:6} value={f.value.raw:12} "
            f"verdict={f.verdict.value} conf={f.confidence}"
        )
        for c in f.failed_checks:
            print(f"       ! {c.name}: {c.detail}")
    print("summary:", json.dumps(summarize(fields), ensure_ascii=False))


if __name__ == "__main__":
    main()
