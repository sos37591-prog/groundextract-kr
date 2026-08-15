"""Tests for the optional Docling adapter.

Docling itself is an optional extra, so the suite is split:

* Mock-based tests (no docling needed, always run): adapter construction, the
  missing-dependency ``ImportError`` hint, span collection via a fake docling
  module, ``find_span`` and ``enrich``.
* Real-docling tests guarded by ``pytest.importorskip("docling")`` inside each
  test — the whole docling-dependent portion is skipped when not installed.
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from groundextract.adapters import DoclingAdapter, SpanInfo
from groundextract.adapters.docling_adapter import _INSTALL_HINT
from groundextract.models import BBox, ExtractedValue

# --- no-docling tests (mock only, must always pass) ----------------------------


def test_adapter_constructs_without_docling():
    adapter = DoclingAdapter()
    assert adapter.full_text == ""
    assert adapter.spans == []


def test_missing_docling_raises_install_hint():
    adapter = DoclingAdapter()
    # ``None`` entries in sys.modules force ImportError even if docling is
    # actually installed, so this test is environment-independent.
    blocked = {"docling": None, "docling.document_converter": None}
    with mock.patch.dict(sys.modules, blocked):
        with pytest.raises(ImportError, match=r"pip install groundextract\[docling\]"):
            adapter.convert("does-not-exist.pdf")
    assert "pip install groundextract[docling]" in _INSTALL_HINT


def _fake_docling_modules(converter_cls: type) -> dict[str, types.ModuleType]:
    pkg = types.ModuleType("docling")
    sub = types.ModuleType("docling.document_converter")
    sub.DocumentConverter = converter_cls  # type: ignore[attr-defined]
    pkg.document_converter = sub  # type: ignore[attr-defined]
    return {"docling": pkg, "docling.document_converter": sub}


class _FakeConverter:
    """Mimics docling's DocumentConverter closely enough for the adapter."""

    def convert(self, source: str) -> types.SimpleNamespace:
        prov = types.SimpleNamespace(
            page_no=2,
            bbox=types.SimpleNamespace(l=10.0, t=20.0, r=110.0, b=32.0),
        )
        with_prov = types.SimpleNamespace(text="공급가액 1,000,000원", prov=[prov])
        without_prov = types.SimpleNamespace(text="세액 100,000원", prov=[])
        doc = types.SimpleNamespace(
            texts=[with_prov, without_prov],
            export_to_text=lambda: "공급가액 1,000,000원\n세액 100,000원",
        )
        return types.SimpleNamespace(document=doc)


def test_convert_with_fake_docling_collects_text_and_spans():
    adapter = DoclingAdapter()
    with mock.patch.dict(sys.modules, _fake_docling_modules(_FakeConverter)):
        result = adapter.convert("dummy.pdf")

    assert result["full_text"] == "공급가액 1,000,000원\n세액 100,000원"
    assert adapter.full_text == result["full_text"]

    spans = result["spans"]
    assert len(spans) == 2
    assert spans[0].page == 2
    assert spans[0].bbox == BBox(2, 10.0, 20.0, 110.0, 32.0)
    assert spans[1].page is None
    assert spans[1].bbox is None


class _NoTextExportConverter:
    """Old/odd API: no export methods, spans only -> text is rebuilt from spans."""

    def convert(self, source: str) -> types.SimpleNamespace:
        item = types.SimpleNamespace(text="합계금액 1,100,000원", prov=None)
        doc = types.SimpleNamespace(texts=[item])
        return types.SimpleNamespace(document=doc)


def test_convert_falls_back_to_span_text_when_export_missing():
    adapter = DoclingAdapter()
    with mock.patch.dict(sys.modules, _fake_docling_modules(_NoTextExportConverter)):
        result = adapter.convert("dummy.pdf")
    assert result["full_text"] == "합계금액 1,100,000원"
    assert result["spans"][0].bbox is None


def test_find_span_substring_search():
    adapter = DoclingAdapter()
    adapter.spans = [
        SpanInfo(text="공급가액 1,000,000원", page=1, bbox=BBox(1, 0.0, 0.0, 100.0, 12.0)),
        SpanInfo(text="세액 100,000원", page=1, bbox=None),
    ]
    hit = adapter.find_span("1,000,000원")
    assert hit is adapter.spans[0]
    assert adapter.find_span("  세액 100,000원  ") is adapter.spans[1]  # stripped
    assert adapter.find_span("9,999,999원") is None
    assert adapter.find_span("") is None


def test_enrich_fills_page_and_bbox_from_quote():
    adapter = DoclingAdapter()
    box = BBox(3, 5.0, 6.0, 7.0, 8.0)
    adapter.spans = [SpanInfo(text="합계금액 1,100,000원", page=3, bbox=box)]

    grounded = ExtractedValue(
        field="total", raw="1,100,000", grounding_quote="합계금액 1,100,000원"
    )
    via_raw = ExtractedValue(field="total2", raw="1,100,000원")
    hallucinated = ExtractedValue(field="vat", raw="9,999,999원")
    already_placed = ExtractedValue(
        field="total3",
        raw="1,100,000원",
        page=9,
        bbox=BBox(9, 0.0, 0.0, 1.0, 1.0),
    )

    out = adapter.enrich([grounded, via_raw, hallucinated, already_placed])
    assert out[0] is grounded  # in-place, same list back

    assert grounded.page == 3
    assert grounded.bbox == box
    assert via_raw.page == 3  # falls back to raw when no quote
    assert hallucinated.page is None
    assert hallucinated.bbox is None
    assert already_placed.page == 9  # existing provenance is never overwritten
    assert already_placed.bbox == BBox(9, 0.0, 0.0, 1.0, 1.0)


# --- docling-installed tests (skipped when the extra is absent) ----------------


def test_real_docling_import_resolves():
    pytest.importorskip("docling")
    from groundextract.adapters.docling_adapter import _import_docling

    converter_cls = _import_docling()
    assert converter_cls.__name__ == "DocumentConverter"


def test_real_docling_converter_instantiates_lazily():
    pytest.importorskip("docling")
    adapter = DoclingAdapter()
    assert adapter._converter is None  # nothing imported/instantiated until convert()
