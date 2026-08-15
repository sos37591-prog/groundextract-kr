"""Docling PDF adapter: PDF -> full text + per-span page/bbox provenance.

Docling is an *optional* dependency (``pip install groundextract[docling]``);
it is imported lazily inside :meth:`DoclingAdapter.convert` so the rest of the
package (gate, rules, bench) keeps working without it.

The Docling API has shifted between releases, so every access to the converted
document is defensive (``getattr``/``hasattr``). Worst case the adapter still
returns the full text with no layout info — the gate only *needs* text; page
and bbox are a bonus for the highlight viewer.

Coordinates are copied from Docling as-is (``l/t/r/b`` -> ``x0/y0/x1/y1``);
depending on the Docling version the origin may be top-left or bottom-left.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import BBox, ExtractedValue

_INSTALL_HINT = (
    "docling is required for DoclingAdapter but is not installed. "
    "Install the optional extra: pip install groundextract[docling]"
)


def _import_docling() -> Any:
    """Import Docling's ``DocumentConverter`` lazily, with an install hint."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc
    return DocumentConverter


@dataclass
class SpanInfo:
    """One text item from the converted document plus its provenance."""

    text: str
    page: int | None = None
    bbox: BBox | None = None


class DoclingAdapter:
    """Converts a PDF with Docling and maps extracted values back to layout.

    Usage::

        adapter = DoclingAdapter()
        result = adapter.convert("invoice.pdf")   # {"full_text": ..., "spans": [...]}
        values = extractor.extract(result["full_text"], doc_type="tax_invoice")
        adapter.enrich(values)                    # fills page/bbox where possible
    """

    def __init__(self) -> None:
        self.full_text: str = ""
        self.spans: list[SpanInfo] = []
        self._converter: Any = None

    # --- conversion ------------------------------------------------------------

    def convert(self, pdf_path: str | Path) -> dict[str, Any]:
        """Convert ``pdf_path`` and return ``{"full_text": str, "spans": [SpanInfo]}``.

        Raises ``ImportError`` (with an install hint) when docling is missing.
        """
        converter_cls = _import_docling()
        if self._converter is None:
            self._converter = converter_cls()
        result = self._converter.convert(str(pdf_path))
        doc = getattr(result, "document", None)
        if doc is None:
            doc = result
        self.spans = self._collect_spans(doc)
        self.full_text = self._export_text(doc)
        if not self.full_text and self.spans:
            # Fallback: reconstruct the text from the individual spans.
            self.full_text = "\n".join(s.text for s in self.spans)
        return {"full_text": self.full_text, "spans": self.spans}

    @staticmethod
    def _export_text(doc: Any) -> str:
        """Best-effort plain-text export across Docling versions."""
        for method in ("export_to_text", "export_to_markdown"):
            export = getattr(doc, method, None)
            if not callable(export):
                continue
            try:
                out = export()
            except Exception:
                continue
            if isinstance(out, str) and out:
                return out
        return ""

    @classmethod
    def _collect_spans(cls, doc: Any) -> list[SpanInfo]:
        """Collect text items with page/bbox provenance, tolerating API drift."""
        spans: list[SpanInfo] = []
        for item in cls._iter_text_items(doc):
            text = getattr(item, "text", None)
            if not text:
                continue
            page, bbox = cls._prov_page_bbox(item)
            spans.append(SpanInfo(text=str(text), page=page, bbox=bbox))
        return spans

    @staticmethod
    def _iter_text_items(doc: Any) -> list[Any]:
        # TODO(W4/viewer): also collect table-cell items (doc.tables) — invoice
        # amounts usually live inside tables, so span/bbox lookup misses them
        # today (verified 2026-07-18: gate passes via full_text, bbox=None).
        items = getattr(doc, "texts", None)
        if items:
            return list(items)
        iterate = getattr(doc, "iterate_items", None)
        if not callable(iterate):
            return []
        try:
            # iterate_items() yields (item, level) tuples in recent docling-core.
            return [e[0] if isinstance(e, tuple) else e for e in iterate()]
        except Exception:
            return []

    @staticmethod
    def _prov_page_bbox(item: Any) -> tuple[int | None, BBox | None]:
        """Extract ``(page, BBox|None)`` from an item's first provenance entry."""
        prov = getattr(item, "prov", None)
        if not prov:
            return None, None
        try:
            first = prov[0]
        except (IndexError, TypeError):
            return None, None

        page: int | None = None
        for attr in ("page_no", "page"):
            raw_page = getattr(first, attr, None)
            if raw_page is not None:
                try:
                    page = int(raw_page)
                except (TypeError, ValueError):
                    page = None
                break

        raw_bbox = getattr(first, "bbox", None)
        coords: tuple[float, float, float, float] | None = None
        if raw_bbox is not None:
            try:
                if all(hasattr(raw_bbox, a) for a in ("l", "t", "r", "b")):
                    coords = (
                        float(raw_bbox.l),
                        float(raw_bbox.t),
                        float(raw_bbox.r),
                        float(raw_bbox.b),
                    )
                elif all(hasattr(raw_bbox, a) for a in ("x0", "y0", "x1", "y1")):
                    coords = (
                        float(raw_bbox.x0),
                        float(raw_bbox.y0),
                        float(raw_bbox.x1),
                        float(raw_bbox.y1),
                    )
            except (TypeError, ValueError):
                coords = None

        if coords is None:
            return page, None
        return page, BBox(page if page is not None else 0, *coords)

    # --- span lookup / enrichment ----------------------------------------------

    def find_span(self, text: str) -> SpanInfo | None:
        """Return the first collected span containing ``text`` (substring search)."""
        needle = text.strip() if text else ""
        if not needle:
            return None
        for span in self.spans:
            if needle in span.text:
                return span
        return None

    def enrich(self, values: list[ExtractedValue]) -> list[ExtractedValue]:
        """Fill ``page``/``bbox`` on each value from its quote (in place).

        The grounding quote is tried first, then ``raw``. Values whose text is
        not found in any span — or that already carry page/bbox — are left
        untouched. Returns the same list for chaining.
        """
        for value in values:
            span = None
            if value.grounding_quote:
                span = self.find_span(value.grounding_quote)
            if span is None:
                span = self.find_span(value.raw)
            if span is None:
                continue
            if value.page is None:
                value.page = span.page
            if value.bbox is None:
                value.bbox = span.bbox
        return values
