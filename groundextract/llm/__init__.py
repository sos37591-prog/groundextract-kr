"""LLM/OCR adapters. ``MockExtractor`` is key-free; ``OllamaExtractor`` uses a local Ollama."""

from .base import Extractor
from .mock import MockExtractor
from .openweight import OllamaExtractor

__all__ = ["Extractor", "MockExtractor", "OllamaExtractor"]
