"""Document conversion adapters (optional heavy backends).

Heavy third-party libraries (Docling, ...) are imported lazily inside the
adapter methods, so importing this package never requires them. Missing
optional dependencies raise ``ImportError`` with an install hint at call time.
"""

from .docling_adapter import DoclingAdapter, SpanInfo

__all__ = ["DoclingAdapter", "SpanInfo"]
