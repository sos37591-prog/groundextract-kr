"""Real-world end-to-end verification: PDF -> Docling -> Ollama LLM -> gate.

This is the "item 2" proof for the contest roadmap: the pipeline runs with a
REAL PDF (rendered from HTML by headless Chrome) and a REAL open-weight LLM
(Qwen2.5 7B, Apache-2.0, served by a local Ollama), not mocks.

Steps
  1. DoclingAdapter converts bench/sample_pdf/tax_invoice_sample.pdf
     -> full_text + spans (page/bbox provenance).
  2. OllamaExtractor extracts {supply, vat, total} with verbatim quotes.
  3. adapter.enrich() maps each value back to its page/bbox.
  4. run_gate verifies grounding + arithmetic -> expect all VERIFIED.
  5. Negative control: tamper the VAT value -> expect DISCARDED.

Run:  python bench/verify_real.py [--model qwen2.5:7b]
Exit code 0 only if both the positive and negative checks pass.

Default model is ``qwen2.5:7b`` (Apache-2.0). See
``groundextract/llm/openweight.py`` for why the 3B/72B Qwen2.5 sizes are
deliberately avoided (Qwen-Research = non-commercial) and why 7B is the
recommended minimum.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from groundextract.adapters import DoclingAdapter  # noqa: E402
from groundextract.gate import run_gate, summarize  # noqa: E402
from groundextract.llm.openweight import OllamaExtractor  # noqa: E402
from groundextract.models import ExtractedValue, Verdict  # noqa: E402
from groundextract.rules import load_rule_pack  # noqa: E402

PDF = ROOT / "bench" / "sample_pdf" / "tax_invoice_sample.pdf"
RULES = ROOT / "rules" / "tax_invoice.yaml"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b", help="Ollama model tag")
    ap.add_argument("--host", default="http://localhost:11434")
    args = ap.parse_args()

    # -- 1. Docling: PDF -> text + layout ---------------------------------------
    print(f"[1/5] Docling convert: {PDF.name}")
    adapter = DoclingAdapter()
    result = adapter.convert(PDF)
    text = result["full_text"]
    spans = result["spans"]
    with_bbox = sum(1 for s in spans if s.bbox is not None)
    print(f"      text: {len(text)} chars, spans: {len(spans)} ({with_bbox} with bbox)")
    excerpt = " / ".join(line for line in text.splitlines() if line.strip())[:200]
    print(f"      excerpt: {excerpt}")
    if "1,000,000" not in text.replace(" ", "") and "1000000" not in text.replace(",", ""):
        print("      WARN: expected amount not found in extracted text")

    # -- 2. Ollama: real open-weight extraction ----------------------------------
    print(f"[2/5] Ollama extract (model={args.model})")
    extractor = OllamaExtractor(model=args.model, host=args.host)
    values = extractor.extract(text, doc_type="tax_invoice")
    for v in values:
        print(f"      {v.field:7} raw={v.raw!r:16} number={v.number} quote={v.grounding_quote!r}")
    if not values:
        print("FAIL: extractor returned no values")
        return 1

    # -- 3. enrich: map values back to page/bbox ---------------------------------
    print("[3/5] enrich page/bbox from Docling spans")
    adapter.enrich(values)
    for v in values:
        print(f"      {v.field:7} page={v.page} bbox={v.bbox}")

    # -- 4. gate: expect all verified --------------------------------------------
    print("[4/5] gate (positive: real extraction should verify)")
    pack = load_rule_pack(RULES)
    fields = run_gate(values, text, pack)
    for f in fields:
        mark = "OK " if f.verdict is Verdict.VERIFIED else "XX "
        print(f"      {mark}{f.field:7} verdict={f.verdict.value} conf={f.confidence}")
        for c in f.failed_checks:
            print(f"          ! {c.name}: {c.detail}")
    print(f"      summary: {summarize(fields)}")
    positive_ok = all(f.verdict is Verdict.VERIFIED for f in fields)

    # -- 5. negative control: tampered VAT must die ------------------------------
    print("[5/5] gate (negative: tampered VAT must be discarded)")
    tampered = [
        ExtractedValue(v.field, v.raw, v.number, v.grounding_quote) for v in values
    ]
    for v in tampered:
        if v.field == "vat":
            v.raw, v.number = "250,000원", 250_000.0
            v.grounding_quote = "세액 250,000원"
    t_fields = {f.field: f for f in run_gate(tampered, text, pack)}
    vat = t_fields.get("vat")
    negative_ok = vat is not None and vat.verdict is Verdict.DISCARDED and vat.confidence == 0.0
    v_verdict = vat.verdict.value if vat else "?"
    v_conf = vat.confidence if vat else "?"
    print(f"      vat verdict={v_verdict} conf={v_conf}")

    print()
    if positive_ok and negative_ok:
        print("PASS: real PDF + real open-weight LLM + gate all verified end-to-end.")
        return 0
    print(f"FAIL: positive_ok={positive_ok} negative_ok={negative_ok}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
