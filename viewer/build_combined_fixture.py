"""Build the COMBINED viewer fixture: 전자세금계산서 + 표준재무상태표 in one file.

Writes ``window.GEK_DOCS = [invoice_doc, balance_sheet_doc]`` so the demo viewer
can switch between both real, PII-masked documents with a tab. Each doc carries
its own clean/injected gate results and shows a *different* failure mode:

  • 세금계산서: a hallucinated VAT (50,000) that never appears on the invoice —
    grounding FAILS, and the arithmetic pack indicts it (합계금액 is spared: it
    cannot explain both violations).
  • 재무상태표: a one-digit misread of 비유동자산 that IS present in the OCR text —
    grounding PASSES, only the cross-field ARITHMETIC invariant catches it.

Privacy: both documents are de-identified. The 세금계산서 PII source was deleted,
so its redacted PNG is reused as-is (its amount boxes are pinned constants). The
재무상태표 is rendered from ``viewer/samples/balance_sheet_sample.pdf`` — a
synthetic sample committed to this repo: it keeps the NTS standard form layout
but every figure is fabricated (all accounting identities still hold), and the
issuer imagery, barcodes and company identifiers were removed at object level.
That is why this script needs no private input and anyone can reproduce the
fixture. ``GEK_BALANCE_PDF`` may still point it at a different source document.

Run (needs pypdfium2 + Pillow):  python viewer/build_combined_fixture.py

PDF rendering and text extraction go through **pypdfium2** (BSD-3-Clause /
Apache-2.0), not PyMuPDF. PyMuPDF is AGPL-3.0, and an AGPL package anywhere in
the dependency graph — even in an optional, maintainer-only extra like this one —
is a licence-compatibility question this Apache-2.0 project should not have to
answer. pdfium gives the same two things this script needs (a rasterized page and
word boxes), so there is nothing to trade away.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from groundextract.gate import run_gate, summarize  # noqa: E402
from groundextract.models import ExtractedValue  # noqa: E402
from groundextract.rules import load_rule_pack  # noqa: E402

FIX = ROOT / "viewer" / "fixtures" / "image_data.js"


def _emit(fields, box_by_field: dict, label_by_field: dict, pack=None) -> dict:
    out = []
    for f in fields:
        out.append({
            "field": f.field,
            "label": label_by_field[f.field],
            "value": f.value.raw,
            "verdict": f.verdict.value,
            "confidence": f.confidence,
            "box": box_by_field.get(f.field),
            "failed": [{"name": c.name, "detail": c.detail} for c in f.failed_checks],
        })
    return {"summary": summarize(fields, pack), "fields": out}


# --------------------------------------------------------------------------- #
# ① 전자세금계산서 (masked PNG reused; amount boxes are pinned constants)
# --------------------------------------------------------------------------- #
INV_IMG = ROOT / "viewer" / "assets" / "tax_invoice_demo.png"
INV_FIELDS = [
    ("supply", "공급가액", 150_000, [452, 666, 543, 706]),
    ("vat", "세액(부가세)", 15_000, [706, 668, 785, 705]),
    ("total", "합계금액", 165_000, [290, 1057, 381, 1098]),
]
INV_TEXT = (
    "전자세금계산서 상세조회  작성일자 2025-12-02  공급가액 150,000  세액 15,000  "
    "품목 소프트웨어 유지보수  단가 150,000  공급가액 150,000  세액 15,000  "
    "합계금액 165,000  현금  수표  어음  외상미수금"
)


def build_invoice() -> dict:
    # De-identify the retained invoice screenshot: the 공급자/공급받는자 block was redacted
    # when the demo PNG was first built and the line-item description was replaced with a
    # generic one; here we additionally mask the NTS 승인번호 (an issuer-identifying
    # document number). Idempotent on re-runs.
    img = Image.open(INV_IMG).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("malgun.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    approval_box = (375, 130, 698, 180)  # 승인번호 value cell @ 1699x1189
    d.rectangle(approval_box, fill=(232, 236, 246), outline=(150, 160, 185), width=1)
    d.text(((approval_box[0] + approval_box[2]) / 2, (approval_box[1] + approval_box[3]) / 2),
           "비공개", fill=(120, 130, 155), font=font, anchor="mm")
    img.save(INV_IMG)
    W, H = img.size
    pack = load_rule_pack(ROOT / "rules" / "tax_invoice.yaml")
    boxes = {fid: box for fid, _lbl, _n, box in INV_FIELDS}
    labels = {fid: lbl for fid, lbl, _n, _b in INV_FIELDS}

    def run(vat_num: int, vat_raw: str) -> dict:
        vals = []
        for fid, _lbl, num, _box in INV_FIELDS:
            if fid == "vat":
                vals.append(ExtractedValue(fid, vat_raw, float(vat_num)))
            else:
                vals.append(ExtractedValue(fid, f"{num:,}원", float(num)))
        return _emit(run_gate(vals, INV_TEXT, pack), boxes, labels, pack)

    clean = run(15_000, "15,000원")
    injected = run(50_000, "50,000원")  # hallucinated VAT — not on the invoice
    return {
        "id": "tax_invoice", "title": "전자세금계산서",
        "image": "assets/tax_invoice_demo.png",
        "width": W, "height": H, "clean": clean, "injected": injected,
        "story": ("환각 세액(50,000원)은 문서에 없음 → 근거 실패 + 산술 위반. "
                  "합계금액은 두 위반을 설명하지 못하므로 폐기되지 않음"),
        "source": "비식별 캡처 (공급자·공급받는자·승인번호 마스킹 · 품목 일반화)",
    }


# --------------------------------------------------------------------------- #
# ② 표준재무상태표 (rendered from the committed synthetic sample)
# --------------------------------------------------------------------------- #
SAMPLE_PDF = ROOT / "viewer" / "samples" / "balance_sheet_sample.pdf"
SRC_PDF = Path(os.environ.get("GEK_BALANCE_PDF", str(SAMPLE_PDF)))
BS_PAGE = 0
Z = 2.0

# This page is rendered wholesale — there is no masking step for it, unlike the
# tax invoice above, because the committed sample is synthetic and has nothing to
# mask. That makes the output path a safety question rather than a preference: a
# real 재무제표 passed via GEK_BALANCE_PDF would otherwise be rasterized straight
# over a *tracked* asset, putting 사업자등록번호·법인명·전 계정과목 금액 one
# `git add .` away from a public repository (and GitHub keeps old blobs reachable
# even after a force-push). So only the synthetic sample may write to the tracked
# demo asset; anything else goes to gitignored local/.
_FROM_SAMPLE = SRC_PDF.resolve() == SAMPLE_PDF.resolve()
BS_IMG = (
    ROOT / "viewer" / "assets" / "balance_sheet_demo.png"
    if _FROM_SAMPLE
    else ROOT / "local" / "balance_sheet_demo.png"
)
BS_FIELDS = [
    ("current_assets", "유동자산", 398_180_000, "L"),
    ("noncurrent_assets", "비유동자산", 39_431_995, "L"),
    ("total_assets", "자산총계", 437_611_995, "L"),
    ("total_liabilities", "부채총계", 109_500_000, "R"),
    ("total_equity", "자본총계", 328_111_995, "R"),
    ("total_liab_equity", "부채와자본총계", 437_611_995, "R"),
]


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _words(page, textpage) -> list[tuple[float, float, float, float, str]]:
    """Whitespace-delimited words as ``(x0, y0, x1, y1, text)`` in page points.

    pdfium exposes characters, not words, and puts the origin at the bottom-left
    with y growing upward. Both are converted here so the rest of the script (and
    the boxes it writes into the fixture) keeps working in the top-left,
    y-down space the viewer overlays expect.
    """
    _, page_height = page.get_size()
    out: list[tuple[float, float, float, float, str]] = []
    chars: list[str] = []
    box: list[float] | None = None

    def flush() -> None:
        nonlocal box
        if chars and box is not None:
            out.append((box[0], box[1], box[2], box[3], "".join(chars)))
        chars.clear()
        box = None

    for i in range(textpage.count_chars()):
        ch = textpage.get_text_range(i, 1)
        if not ch or ch.isspace():
            flush()
            continue
        left, bottom, right, top = textpage.get_charbox(i)
        # bottom-left origin -> top-left origin
        y0, y1 = page_height - top, page_height - bottom
        if box is None:
            box = [left, y0, right, y1]
        else:
            box = [min(box[0], left), min(box[1], y0), max(box[2], right), max(box[3], y1)]
        chars.append(ch)
    flush()
    return out


def build_balance() -> dict:
    if not SRC_PDF.exists():
        raise SystemExit(
            f"balance-sheet source not found: {SRC_PDF}\n"
            "Expected the committed synthetic sample at viewer/samples/, or set "
            "GEK_BALANCE_PDF to your own document."
        )
    if not _FROM_SAMPLE:
        print(
            f"! {SRC_PDF.name} is not the committed synthetic sample.\n"
            f"! Rendering it unmasked to {BS_IMG} (gitignored) instead of the\n"
            f"! tracked demo asset. Review the image before publishing anything\n"
            f"! derived from it.",
            file=sys.stderr,
        )
    doc = pdfium.PdfDocument(SRC_PDF)
    page = doc[BS_PAGE]
    img = page.render(scale=Z).to_pil().convert("RGB")
    W, H = img.size
    BS_IMG.parent.mkdir(parents=True, exist_ok=True)
    img.save(BS_IMG)

    textpage = page.get_textpage()
    words = _words(page, textpage)

    def box_for(num: int, side: str | None):
        key = str(num)
        hits = [w for w in words if _digits(w[4]) == key]
        if side == "L":
            hits = [w for w in hits if w[0] * Z < 700] or hits
        elif side == "R":
            hits = [w for w in hits if w[0] * Z >= 700] or hits
        if not hits:
            return None
        hits.sort(key=lambda w: (round(w[1]), round(w[0])))
        w = hits[0]
        return [round(w[0] * Z, 1), round(w[1] * Z, 1), round(w[2] * Z, 1), round(w[3] * Z, 1)]

    boxes = {fid: box_for(num, side) for fid, _lbl, num, side in BS_FIELDS}
    labels = {fid: lbl for fid, lbl, _n, _s in BS_FIELDS}
    real_text = textpage.get_text_range()
    pack = load_rule_pack(ROOT / "rules" / "balance_sheet.yaml")

    def run(noncur_num: int, noncur_raw: str, text: str) -> dict:
        vals = []
        for fid, _lbl, num, _side in BS_FIELDS:
            if fid == "noncurrent_assets":
                vals.append(ExtractedValue(fid, noncur_raw, float(noncur_num)))
            else:
                vals.append(ExtractedValue(fid, f"{num:,}원", float(num)))
        return _emit(run_gate(vals, text, pack), boxes, labels, pack)

    clean = run(39_431_995, "39,431,995원", real_text)
    injected = run(39_431_895, "39,431,895원", real_text.replace("39,431,995", "39,431,895"))
    return {
        "id": "balance_sheet", "title": "표준재무상태표",
        "image": "assets/balance_sheet_demo.png",
        "width": W, "height": H, "clean": clean, "injected": injected,
        "story": ("비유동자산 한 자리 오독은 OCR 텍스트에도 존재 → 근거 통과, 산술만 적발. "
                  "대차평형이 자산총계를 입증하므로 덧셈 항 2개만 폐기"),
        "source": "합성 샘플 (표준 서식 · 모든 금액 가상 · 회계 항등식 성립)",
    }


def main() -> int:
    docs = [build_invoice(), build_balance()]
    FIX.write_text(
        "window.GEK_DOCS = " + json.dumps(docs, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    for d in docs:
        print(f"  {d['id']:14} {d['width']}x{d['height']}  "
              f"clean {d['clean']['summary']}  injected {d['injected']['summary']}")
    print(f"wrote {FIX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
