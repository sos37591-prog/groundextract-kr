"""OllamaExtractor tests — fully offline: ``urllib.request.urlopen`` is monkeypatched.

Covers: happy-path parsing (incl. request payload shape), scalar/missing field
tolerance, invalid model JSON, malformed server envelope, server-down
ConnectionError, HTTP errors, unknown doc_type, and gate integration.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from groundextract import Verdict, load_rule_pack, run_gate
from groundextract.llm import Extractor, OllamaExtractor

RULES = Path(__file__).resolve().parent.parent / "rules" / "tax_invoice.yaml"

DOC = (
    "전자세금계산서\n"
    "공급가액  1,000,000원\n"
    "세액        100,000원\n"
    "합계금액  1,100,000원\n"
)

GOOD_CONTENT = json.dumps(
    {
        "supply": {"value": "1,000,000원", "quote": "공급가액  1,000,000원"},
        "vat": {"value": "100,000원", "quote": "세액        100,000원"},
        "total": {"value": "1,100,000원", "quote": "합계금액  1,100,000원"},
    },
    ensure_ascii=False,
)


def _envelope(content: str) -> bytes:
    """A non-streaming Ollama /api/chat response body wrapping ``content``."""
    return json.dumps(
        {
            "model": "qwen2.5:7b",
            "message": {"role": "assistant", "content": content},
            "done": True,
        }
    ).encode("utf-8")


class _FakeResponse:
    """Minimal stand-in for the object ``urlopen`` returns (context manager)."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _patch_urlopen(monkeypatch, body: bytes, calls: list | None = None) -> None:
    """Replace urlopen with a fake returning ``body``; record (request, timeout)."""

    def fake_urlopen(request, timeout=None):
        if calls is not None:
            calls.append((request, timeout))
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


# --- protocol / construction ---------------------------------------------------


def test_conforms_to_extractor_protocol():
    assert isinstance(OllamaExtractor(), Extractor)


def test_unknown_doc_type_raises_before_any_network():
    with pytest.raises(ValueError, match="doc_type"):
        OllamaExtractor().extract("x", doc_type="receipt")


# --- happy path ----------------------------------------------------------------


def test_happy_path_parses_values_and_sends_expected_payload(monkeypatch):
    calls: list = []
    _patch_urlopen(monkeypatch, _envelope(GOOD_CONTENT), calls)

    extractor = OllamaExtractor(model="qwen2.5:7b", timeout=5.0)
    values = extractor.extract(DOC, doc_type="tax_invoice")

    assert [v.field for v in values] == ["supply", "vat", "total"]
    by = {v.field: v for v in values}
    assert by["supply"].raw == "1,000,000원"
    assert by["supply"].number == 1_000_000
    assert by["supply"].grounding_quote == "공급가액  1,000,000원"
    assert by["vat"].number == 100_000
    assert by["total"].number == 1_100_000

    # exactly one POST to /api/chat, format=json, non-streaming, doc in prompt
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == "http://localhost:11434/api/chat"
    assert timeout == 5.0
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "qwen2.5:7b"
    assert payload["format"] == "json"
    assert payload["stream"] is False
    user_msg = payload["messages"][-1]["content"]
    assert DOC in user_msg
    assert "quote" in user_msg  # verbatim-quote instruction is present


def test_custom_host_model_and_statement_fields(monkeypatch):
    content = json.dumps(
        {
            "debit_total": {"value": "5,000,000", "quote": "차변 합계 5,000,000"},
            "credit_total": {"value": "5,000,000", "quote": "대변 합계 5,000,000"},
        },
        ensure_ascii=False,
    )
    calls: list = []
    _patch_urlopen(monkeypatch, _envelope(content), calls)

    extractor = OllamaExtractor(model="llama3.1:8b", host="http://10.0.0.5:11434/")
    values = extractor.extract("차변 합계 5,000,000\n대변 합계 5,000,000\n", doc_type="statement")

    assert [v.field for v in values] == ["debit_total", "credit_total"]
    request, _ = calls[0]
    assert request.full_url == "http://10.0.0.5:11434/api/chat"  # trailing slash stripped
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "llama3.1:8b"
    user_msg = payload["messages"][-1]["content"]
    assert "debit_total" in user_msg and "credit_total" in user_msg


def test_parsed_values_pass_the_gate_end_to_end(monkeypatch):
    _patch_urlopen(monkeypatch, _envelope(GOOD_CONTENT))
    values = OllamaExtractor().extract(DOC, doc_type="tax_invoice")
    fields = run_gate(values, DOC, load_rule_pack(RULES))
    assert all(f.verdict is Verdict.VERIFIED for f in fields)


# --- tolerant parsing ----------------------------------------------------------


def test_scalar_entries_and_missing_fields_are_tolerated(monkeypatch):
    # model omitted 'vat' and skipped the {value, quote} envelope for the rest
    content = json.dumps({"supply": "1,000,000원", "total": 1100000}, ensure_ascii=False)
    _patch_urlopen(monkeypatch, _envelope(content))

    values = OllamaExtractor().extract(DOC, doc_type="tax_invoice")
    by = {v.field: v for v in values}

    assert set(by) == {"supply", "total"}  # vat simply absent, no crash
    assert by["supply"].grounding_quote is None  # falls back to full-text grounding
    assert by["supply"].number == 1_000_000
    assert by["total"].raw == "1100000"
    assert by["total"].number == 1_100_000


def test_non_numeric_value_keeps_raw_with_number_none(monkeypatch):
    content = json.dumps({"supply": {"value": "미기재", "quote": "미기재"}}, ensure_ascii=False)
    _patch_urlopen(monkeypatch, _envelope(content))

    values = OllamaExtractor().extract(DOC, doc_type="tax_invoice")
    assert values[0].raw == "미기재"
    assert values[0].number is None


def test_fields_wrapper_object_is_unwrapped(monkeypatch):
    content = json.dumps({"fields": json.loads(GOOD_CONTENT)}, ensure_ascii=False)
    _patch_urlopen(monkeypatch, _envelope(content))
    values = OllamaExtractor().extract(DOC, doc_type="tax_invoice")
    assert [v.field for v in values] == ["supply", "vat", "total"]


# --- error handling ------------------------------------------------------------


def test_invalid_model_json_raises_valueerror(monkeypatch):
    _patch_urlopen(monkeypatch, _envelope("this is not JSON {"))
    with pytest.raises(ValueError, match="valid JSON"):
        OllamaExtractor().extract(DOC, doc_type="tax_invoice")


def test_model_json_array_raises_valueerror(monkeypatch):
    _patch_urlopen(monkeypatch, _envelope("[1, 2, 3]"))
    with pytest.raises(ValueError, match="not an object"):
        OllamaExtractor().extract(DOC, doc_type="tax_invoice")


def test_malformed_server_envelope_raises_valueerror(monkeypatch):
    _patch_urlopen(monkeypatch, b"<html>gateway error</html>")
    with pytest.raises(ValueError, match="Ollama response"):
        OllamaExtractor().extract(DOC, doc_type="tax_invoice")


def test_envelope_without_message_content_raises_valueerror(monkeypatch):
    _patch_urlopen(monkeypatch, json.dumps({"done": True}).encode("utf-8"))
    with pytest.raises(ValueError, match="message content"):
        OllamaExtractor().extract(DOC, doc_type="tax_invoice")


def test_server_down_raises_clear_connectionerror(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ConnectionError, match=r"localhost:11434.*ollama serve") as excinfo:
        OllamaExtractor().extract(DOC, doc_type="tax_invoice")
    assert "qwen2.5:7b" in str(excinfo.value)  # tells the user what to pull


def test_http_error_raises_runtimeerror_with_status(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", None, io.BytesIO(b'{"error":"model not found"}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="404"):
        OllamaExtractor().extract(DOC, doc_type="tax_invoice")
